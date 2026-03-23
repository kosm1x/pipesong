"""Custom Pipecat frame processors for Pipesong."""
import re
import logging

from pipecat.frames.frames import LLMTextFrame
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

logger = logging.getLogger(__name__)

# Match CJK characters, CJK punctuation, and other non-Latin scripts
NON_LATIN_RE = re.compile(
    r'[\u0400-\u04FF'   # Cyrillic
    r'\u0600-\u06FF'    # Arabic
    r'\u0590-\u05FF'    # Hebrew
    r'\u0E00-\u0E7F'    # Thai
    r'\u2E80-\u9FFF'    # CJK Unified
    r'\uF900-\uFAFF'    # CJK Compatibility
    r'\uFE30-\uFE4F'    # CJK Compatibility Forms
    r'\uFF00-\uFFEF'    # Fullwidth Forms (Chinese punctuation ：，。！)
    r'\u3000-\u303F'    # CJK Symbols and Punctuation
    r'\U00020000-\U0002A6DF'  # CJK Extension B
    r']+'
)


class SpanishOnlyFilter(FrameProcessor):
    """Strips non-Latin characters from LLM text output.

    Only intercepts LLMTextFrame. All other frames pass through untouched.
    Replaces stripped characters with a space to preserve word boundaries.
    """

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame) and frame.text:
            original = frame.text
            # Replace non-Latin chars with space (preserve word boundaries)
            cleaned = NON_LATIN_RE.sub(" ", original)
            # Collapse multiple spaces
            cleaned = re.sub(r' +', ' ', cleaned).strip()

            if cleaned != original.strip():
                logger.warning("SpanishOnlyFilter: [%s] -> [%s]",
                             original[:60], cleaned[:60])

            if cleaned:
                await self.push_frame(LLMTextFrame(text=cleaned), direction)
            # Drop if empty after cleaning
        else:
            await self.push_frame(frame, direction)
