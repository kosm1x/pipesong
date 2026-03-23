"""Custom Pipecat frame processors for Pipesong."""
import re
import logging

from pipecat.frames.frames import LLMTextFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

logger = logging.getLogger(__name__)

# Match CJK and other non-Latin scripts
NON_LATIN_RE = re.compile(
    r'[\u0400-\u04FF'   # Cyrillic
    r'\u0600-\u06FF'    # Arabic
    r'\u0590-\u05FF'    # Hebrew
    r'\u0E00-\u0E7F'    # Thai
    r'\u2E80-\u9FFF'    # CJK Unified
    r'\uF900-\uFAFF'    # CJK Compatibility
    r'\uFE30-\uFE4F'    # CJK Compatibility Forms
    r'\uFF00-\uFFEF'    # Fullwidth Forms (Chinese punctuation)
    r'\u3000-\u303F'    # CJK Symbols and Punctuation
    r'\U00020000-\U0002A6DF'  # CJK Extension B
    r']+'
)

# Fix missing spaces from Qwen's tokenizer
MISSING_SPACE_BEFORE_INVERTED = re.compile(r'([a-záéíóúüñA-ZÁÉÍÓÚÜÑ.!?])([¿¡])')
MISSING_SPACE_AFTER_PUNCT = re.compile(r'([.!?,;:])([a-záéíóúüñA-ZÁÉÍÓÚÜÑ¿¡])')
CAMEL_CASE_SPLIT = re.compile(r'([a-záéíóúüñ])([A-ZÁÉÍÓÚÜÑ])')


class SpanishOnlyFilter(FrameProcessor):
    """Cleans LLM text for Spanish TTS.

    Use with TTS_AGGREGATION_MODE=sentence. This filter:
    1. Strips non-Latin characters (Qwen Chinese code-switching)
    2. Fixes missing spaces from Qwen's tokenizer
    3. Converts commas to periods so SENTENCE mode flushes at clause
       boundaries — giving Kokoro shorter, faster chunks while keeping
       enough context for proper Spanish phonemization.
    """

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame) and frame.text:
            text = frame.text

            # Strip non-Latin characters
            text = NON_LATIN_RE.sub(" ", text)

            # Fix missing spaces
            text = MISSING_SPACE_BEFORE_INVERTED.sub(r'\1 \2', text)
            text = MISSING_SPACE_AFTER_PUNCT.sub(r'\1 \2', text)
            text = CAMEL_CASE_SPLIT.sub(r'\1 \2', text)

            # Convert commas to periods — tricks SENTENCE mode into flushing
            # at clause boundaries. Kokoro handles "phrase." the same as "phrase,"
            # but SENTENCE mode treats period as a flush point.
            text = text.replace(",", ".")

            # Collapse multiple spaces
            text = re.sub(r' +', ' ', text)

            if text.strip():
                await self.push_frame(LLMTextFrame(text=text), direction)
        else:
            await self.push_frame(frame, direction)
