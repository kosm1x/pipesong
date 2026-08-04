"""Pipecat voice pipeline factory.

Creates a configured pipeline for each incoming call:
  Audio In → Deepgram Flux STT (transcription + EOT) → LLM Context → vLLM → Kokoro TTS → Audio Out

Targets Pipecat 1.x. Turn-taking is owned by Deepgram Flux (no local VAD).
UNVALIDATED branch (flux-pipecat1x) — see docs/upgrade-flux-pipecat1x-2026-06-17.md.
"""
import logging

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.user_mute import (
    FunctionCallUserMuteStrategy,
    MuteUntilFirstBotCompleteUserMuteStrategy,
)
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.tts_service import TextAggregationMode
from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport

from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

from pipesong.config import settings
from pipesong.processors import (
    DisclosureGate,
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

    # STT — Deepgram Flux (integrated transcription + end-of-turn detection).
    # NOTE: Flux REPLACES Nova-3 entirely (one model does transcription AND turn-taking).
    # UNVALIDATED on this codebase — gate on a Flux-multi vs Nova-3-es Spanish WER A/B and
    # confirm Flux accepts 8 kHz telephony audio. See docs/upgrade-flux-pipecat1x-2026-06-17.md.
    stt = DeepgramFluxSTTService(
        api_key=settings.deepgram_api_key,
        sample_rate=8000,            # VERIFY: Flux accepts 8 kHz telephony input
        flux_encoding="linear16",
        should_interrupt=True,       # Flux drives barge-in (replaces Silero's role)
        settings=DeepgramFluxSTTService.Settings(
            model="flux-general-multi",   # Spanish via the multilingual model
            language_hints=[language],    # bias toward the agent language (es)
            eot_threshold=0.7,            # 0.5-0.9; lower = faster turns, more false ends
            # eager_eot_threshold left OFF (Flux default): it speeds responses but triggers
            # extra speculative LLM calls — a cost regression on a <$0.03/min product.
            # Enable + A/B only after the cost baseline (see upgrade doc §6 / W3).
            eot_timeout_ms=5000,
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

    # Turn-taking is owned by Deepgram Flux (server-side EOT), so the user aggregator
    # uses ExternalUserTurnStrategies instead of a local Silero VAD analyzer.
    # NOTE: the agent-level vad_stop_secs / vad_confidence overrides are now INERT for Flux
    # agents — tune turn behaviour via the STT eot_threshold / eager_eot_threshold instead
    # (future: map those agent columns onto the Flux EOT params).
    if vad_stop_secs is not None or vad_confidence is not None:
        logger.warning(
            "Agent vad_stop_secs/vad_confidence are IGNORED under Deepgram Flux "
            "(Flux owns turn-taking). Tune eot_threshold/eager_eot_threshold instead. "
            "TODO: map these agent columns onto Flux EOT params before merge (audit S1)."
        )
    # User muting — replaces the STTMuteFilter processor removed in Pipecat 1.0.
    # MuteUntilFirstBotComplete: muted from t=0 until the first bot utterance ends,
    # so the legally-required recording disclosure can't be interrupted — including
    # the pre-TTS-audio gap the old FIRST_SPEECH strategy left open (QA 2026-08-04 W2).
    # FunctionCallUserMuteStrategy keys off Pipecat's native function-call frames; it
    # is inert under the prompt-based ToolCallProcessor (as FUNCTION_CALL already was)
    # and becomes live with the native-tool-calling migration (REPROBE Phase 3).
    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=ExternalUserTurnStrategies(),
            user_mute_strategies=[
                MuteUntilFirstBotCompleteUserMuteStrategy(),
                FunctionCallUserMuteStrategy(),
            ],
        ),
    )

    # Filter non-Spanish text from LLM output (Qwen Chinese code-switching fix)
    spanish_filter = SpanishOnlyFilter()

    # Sentence streaming buffer — handles sentence boundary detection and
    # emits TTSSpeakFrames for LLM↔TTS overlap (Phase 4a)
    sentence_buffer = SentenceStreamBuffer()

    # Pipeline: audio in → STT → [disclosure gate] → [user transcript] → [RAG] → context →
    # LLM → [tool processor] → [assistant transcript] → filter → sentence buffer → TTS →
    # [metrics] → audio out
    # (Disclosure/tool-call muting lives on the user aggregator via user_mute_strategies;
    # DisclosureGate keeps pre-disclosure noise out of TranscriptCapture/RAGProcessor,
    # which sit upstream of that mute point — QA 2026-08-04 W1.)
    tool_processor = None
    processors = [
        transport.input(),
        stt,
        DisclosureGate(),
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
