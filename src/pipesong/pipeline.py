"""Pipecat voice pipeline factory.

Creates a configured pipeline for each incoming call:
  Audio In → Deepgram STT → LLM Context → vLLM → Kokoro TTS → Audio Out

Based on official Pipecat Telnyx chatbot example (v0.0.106).
"""
import logging

from pipecat.audio.vad.silero import SileroVADAnalyzer
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

from pipesong.config import settings
from pipesong.processors import SpanishOnlyFilter

logger = logging.getLogger(__name__)


def create_pipeline(
    transport: FastAPIWebsocketTransport,
    system_prompt: str,
    language: str = "es",
    voice: str = "em_alex",
) -> PipelineTask:
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

    # LLM — local vLLM via OpenAI-compatible API
    llm = OpenAILLMService(
        api_key="not-needed",
        base_url=settings.vllm_base_url,
        settings=OpenAILLMService.Settings(
            model=settings.vllm_model,
            system_instruction=system_prompt,
            max_tokens=150,
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
    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    # Filter non-Spanish text from LLM output (Qwen Chinese code-switching fix)
    spanish_filter = SpanishOnlyFilter()

    # Pipeline: audio in → STT → context → LLM → filter → TTS → audio out
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            spanish_filter,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    return task
