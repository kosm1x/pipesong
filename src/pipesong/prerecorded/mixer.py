"""Pre-recorded phrase mixer (roadmap A.1): corpus hits bypass Kokoro.

Two processors around the TTS service:

  ... → SentenceStreamBuffer → DisclosureClipPlayer → PreRecordedMixer → KokoroTTSService
      → PreRecordedClipEmitter → ... → transport.output → LLMAssistantAggregator

``PreRecordedMixer`` (before TTS) swaps a TTSSpeakFrame whose text hits the corpus
for a ``PreRecordedClipFrame``. ``PreRecordedClipEmitter`` (right after TTS) expands
that marker into the frames pipecat 1.12's TTSService emits for one spoken TTSSpeakFrame:

  TTSStartedFrame(context_id, append_to_context)
  TTSAudioRawFrame(clip, context_id)
  MetricsFrame([TTFBMetricsData(processor=<emitter name>)])  synthesis TTFB, ~0 for a clip
  TTSTextFrame(text, aggregated_by=SENTENCE, context_id, append_to_context)
  TTSStoppedFrame(context_id)
  [LLMAssistantPushAggregationFrame]  only outside an LLM response, as TTSService does

so the output transport starts/stops the bot turn on it and the assistant aggregator
writes the phrase to the context exactly as if Kokoro had spoken it (the TTSTextFrame
reaches the aggregator only after the transport has played the audio, and is dropped
with it on barge-in).

Why a marker instead of pushing those frames straight from the mixer: TTSService
consumes every TextFrame it receives (a TTSTextFrame from upstream gets synthesized a
second time), and a ``skip_tts`` text frame is pushed immediately, overtaking the
previous sentence's queued audio, so the phrase would land in the context out of
order. A non-text DataFrame instead goes through TTSService's serialization queue,
in arrival order behind the audio of every sentence before it, and is dropped with
that queue on interruption.
"""
import logging
import time
import uuid
from dataclasses import dataclass

from pipecat.frames.frames import (
    CancelFrame,
    DataFrame,
    EndFrame,
    InterruptionFrame,
    LLMAssistantPushAggregationFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    MetricsFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.text.base_text_aggregator import AggregationType

from pipesong.prerecorded.corpus import Corpus, phrase_key

logger = logging.getLogger(__name__)


@dataclass
class PreRecordedClipFrame(DataFrame):
    """A corpus hit in transit through the TTS service (see module docstring)."""

    text: str
    key: str
    audio: TTSAudioRawFrame
    append_to_context: bool = True


class PreRecordedMixer(FrameProcessor):
    """Swaps a downstream TTSSpeakFrame that hits the corpus for a PreRecordedClipFrame.

    Place immediately before the TTS service. Misses pass through to TTS unchanged.
    ``hits`` / ``misses`` count every TTSSpeakFrame seen (A.2 match precision); each hit
    and the totals (on EndFrame/CancelFrame) are logged at INFO.
    """

    def __init__(self, corpus: Corpus, **kwargs):
        super().__init__(**kwargs)
        self._corpus = corpus
        self.hits = 0
        self.misses = 0

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if direction == FrameDirection.DOWNSTREAM and isinstance(frame, TTSSpeakFrame):
            clip = self._corpus.lookup(frame.text)
            if clip is not None:
                self.hits += 1
                key = phrase_key(self._corpus.voice, frame.text)
                logger.info("mixer: hit %s %r", key, frame.text[:40])
                await self.push_frame(
                    PreRecordedClipFrame(
                        text=frame.text, key=key, audio=clip, append_to_context=frame.append_to_context
                    ),
                    direction,
                )
                return
            self.misses += 1
        elif isinstance(frame, (EndFrame, CancelFrame)):
            logger.info(
                "mixer: %d hit(s), %d miss(es), corpus of %d clip(s)", self.hits, self.misses, len(self._corpus)
            )

        await self.push_frame(frame, direction)


class PreRecordedClipEmitter(FrameProcessor):
    """Expands a PreRecordedClipFrame into TTS-shaped frames. Place right after the TTS service."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Inside an LLM response, its LLMFullResponseEndFrame commits the assistant turn.
        self._in_llm_response = False

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._in_llm_response = True
        elif isinstance(frame, (LLMFullResponseEndFrame, InterruptionFrame)):
            self._in_llm_response = False
        elif direction == FrameDirection.DOWNSTREAM and isinstance(frame, PreRecordedClipFrame):
            received = time.monotonic()
            context_id = str(uuid.uuid4())
            audio = frame.audio
            audio.context_id = context_id
            text = TTSTextFrame(frame.text, aggregated_by=AggregationType.SENTENCE, context_id=context_id)
            text.append_to_context = frame.append_to_context
            text.will_be_spoken = True
            await self.push_frame(
                TTSStartedFrame(context_id=context_id, append_to_context=frame.append_to_context), direction
            )
            await self.push_frame(audio, direction)
            # Synthesis TTFB (marker receipt -> audio push), in the shape TTS services report
            # it: ~0 by construction, NOT time to first audio. MetricsCollector counts these
            # as prerecorded_hits; caller-heard TTFA is its ttfa_ms (user EOT -> BotStarted).
            ttfb = TTFBMetricsData(processor=self.name, value=time.monotonic() - received)
            await self.push_frame(MetricsFrame(data=[ttfb]), direction)
            await self.push_frame(text, direction)
            await self.push_frame(TTSStoppedFrame(context_id=context_id), direction)
            # pipecat 1.12 TTSService (push_text_frames=True) only emits this (on
            # TTSStoppedFrame, tts_service.py:1031) before the first LLM response of a call
            # or after an interrupted one: _llm_response_started is set at :885 and cleared
            # only at :1801, which :1789 skips, or on interruption (:1146). The emitter
            # emits it whenever it is outside a response. No pipesong path sends an
            # append_to_context TTSSpeakFrame outside a response today.
            if frame.append_to_context and not self._in_llm_response:
                await self.push_frame(LLMAssistantPushAggregationFrame(), direction)
            return

        await self.push_frame(frame, direction)
