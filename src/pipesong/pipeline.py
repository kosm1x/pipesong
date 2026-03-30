"""Pipecat voice pipeline factory.

Creates a configured pipeline for each incoming call:
  Audio In → Deepgram STT → LLM Context → vLLM → Kokoro TTS → Audio Out

Based on official Pipecat Telnyx chatbot example (v0.0.106).
"""
import logging

from pipecat.audio.vad.silero import SileroVADAnalyzer, VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.tts_service import TextAggregationMode
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport

from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.filters.stt_mute_filter import STTMuteFilter, STTMuteConfig, STTMuteStrategy

from pipesong.config import settings
from pipesong.processors import (
    MetricsCollector,
    RAGProcessor,
    SentenceStreamBuffer,
    SpanishOnlyFilter,
    ToolCallProcessor,
    TranscriptCapture,
)
from pipesong.services.tools import ToolExecutor, format_tools_prompt

logger = logging.getLogger(__name__)


def create_pipeline(
    transport: FastAPIWebsocketTransport,
    system_prompt: str,
    language: str = "es",
    voice: str = "em_alex",
    call_id=None,
    session_factory=None,
    audio_buffer: AudioBufferProcessor | None = None,
    tools: list[dict] | None = None,
    variables: dict | None = None,
    call_control_id: str | None = None,
    knowledge_base_id=None,
    kb_chunk_count: int = 3,
    kb_similarity_threshold: float = 0.5,
    vad_stop_secs: float | None = None,
    vad_confidence: float | None = None,
) -> tuple["PipelineTask", "ToolCallProcessor | None"]:
    """Build a Pipecat pipeline for a single call."""

    # STT — Deepgram streaming
    stt = DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        audio_passthrough=True,
        sample_rate=8000,
        settings=DeepgramSTTService.Settings(
            language=language,
            model="nova-3",
            smart_format=True,
            interim_results=True,
        ),
    )

    @stt.event_handler("on_error")
    async def on_stt_error(processor, error):
        logger.error("Deepgram STT error: %s", error)

    # Inject tool definitions into system prompt if tools are configured
    full_prompt = system_prompt
    if tools:
        full_prompt += format_tools_prompt(tools)

    # LLM — local vLLM via OpenAI-compatible API
    llm = OpenAILLMService(
        api_key="not-needed",
        base_url=settings.vllm_base_url,
        settings=OpenAILLMService.Settings(
            model=settings.vllm_model,
            system_instruction=full_prompt,
            max_tokens=300 if (tools or knowledge_base_id) else 150,
            frequency_penalty=1.2,
        ),
    )

    # TTS — Kokoro local
    # Text aggregation mode controls when TTS starts generating:
    #   SENTENCE (default): waits for full sentence — 800-1600ms TTFB
    #   TOKEN: generates on each token — lowest latency, may be choppy
    #   WORD: generates on each word — middle ground
    # Configurable via TTS_AGGREGATION_MODE env var
    mode_map = {
        "sentence": TextAggregationMode.SENTENCE,
        "token": TextAggregationMode.TOKEN,
    }
    tts_mode = mode_map.get(settings.tts_aggregation_mode, TextAggregationMode.SENTENCE)
    logger.info("TTS aggregation mode: %s", settings.tts_aggregation_mode)

    tts = KokoroTTSService(
        voice_id=voice,
        text_aggregation_mode=tts_mode,
        settings=KokoroTTSService.Settings(
            voice=voice,
            language="es",
        ),
    )

    # Context + VAD on user aggregator (official pattern)
    # Use agent-level VAD overrides if provided, otherwise Pipecat defaults
    vad_params = VADParams()
    if vad_stop_secs is not None:
        vad_params.stop_secs = vad_stop_secs
    if vad_confidence is not None:
        vad_params.confidence = vad_confidence

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(sample_rate=8000, params=vad_params),
        ),
    )

    # Filter non-Spanish text from LLM output (Qwen Chinese code-switching fix)
    spanish_filter = SpanishOnlyFilter()

    # Sentence streaming buffer — handles sentence boundary detection and
    # emits TTSSpeakFrames for LLM↔TTS overlap (Phase 4a)
    sentence_buffer = SentenceStreamBuffer()

    # Pipeline: audio in → STT → [user transcript] → [RAG] → context → LLM →
    # [tool processor] → [assistant transcript] → filter → sentence buffer → TTS → [metrics] → audio out
    # Suppress interruptions during disclosure (FIRST_SPEECH) and tool execution (FUNCTION_CALL)
    stt_mute = STTMuteFilter(
        config=STTMuteConfig(strategies={STTMuteStrategy.FIRST_SPEECH, STTMuteStrategy.FUNCTION_CALL}),
    )

    tool_processor = None
    processors = [
        transport.input(),
        stt,
        stt_mute,  # After STT: intercepts TranscriptionFrame/InterruptionFrame
    ]
    if call_id and session_factory:
        # User capture between STT and aggregator (catches TranscriptionFrame)
        processors.append(TranscriptCapture(call_id=call_id, session_factory=session_factory))
    if knowledge_base_id and session_factory:
        processors.append(RAGProcessor(
            knowledge_base_id=knowledge_base_id,
            session_factory=session_factory,
            context=context,
            chunk_count=kb_chunk_count,
            threshold=kb_similarity_threshold,
        ))
    processors.extend([
        user_aggregator,
        llm,
    ])
    if tools:
        tool_processor = ToolCallProcessor(
            tools=tools,
            tool_executor=ToolExecutor(),
            context=context,
            llm=llm,
            variables=variables,
            call_control_id=call_control_id,
        )
        processors.append(tool_processor)
    if call_id and session_factory:
        # Assistant capture AFTER tool processor — captures spoken text, not raw tool JSON
        processors.append(TranscriptCapture(call_id=call_id, session_factory=session_factory))
    processors.extend([
        spanish_filter,
        sentence_buffer,
        tts,
    ])
    # MetricsCollector after TTS — captures MetricsFrame from all upstream services
    if call_id and session_factory:
        processors.append(MetricsCollector(call_id=call_id, session_factory=session_factory))
    processors.append(transport.output())
    if audio_buffer:
        processors.append(audio_buffer)
    processors.append(assistant_aggregator)

    pipeline = Pipeline(processors)

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    return task, tool_processor


async def cleanup_pipeline(tool_processor: "ToolCallProcessor | None"):
    """Close resources created by create_pipeline. Call in the WebSocket finally block."""
    if tool_processor and tool_processor._tool_executor:
        await tool_processor._tool_executor.close()
