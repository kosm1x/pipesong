"""Pipecat voice pipeline factory.

Creates a configured pipeline for each incoming call:
  Audio In → Deepgram STT → LLM Context → vLLM → Kokoro TTS → Audio Out
"""
import logging

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.openai import OpenAILLMService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.vad.silero import SileroVADAnalyzer

from pipesong.config import settings

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
        live_options={
            "model": "nova-3",
            "language": language,
            "punctuate": True,
            "smart_format": True,
            "interim_results": True,
        },
    )

    # LLM — local vLLM via OpenAI-compatible API
    llm = OpenAILLMService(
        api_key="not-needed",
        base_url=settings.vllm_base_url,
        model=settings.vllm_model,
    )

    # TTS — Kokoro local
    tts = KokoroTTSService(voice=voice)

    # Conversation context
    messages = [{"role": "system", "content": system_prompt}]
    context = OpenAILLMContext(messages=messages)
    context_aggregator = llm.create_context_aggregator(context)

    # Pipeline: audio in → STT → context → LLM → TTS → audio out
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            allow_interruptions=True,
            enable_metrics=True,
        ),
    )

    return task
