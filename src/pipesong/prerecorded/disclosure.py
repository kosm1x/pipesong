"""Pre-recorded recording-disclosure clips (roadmap A.0).

A clip lives at ``<base_dir>/disclosure/<key>.wav`` where ``key`` is the first 16
hex chars of ``sha256(f"{voice}\n{normalize(text)}")``. The voice is used verbatim
(stripped only); text normalization = Unicode NFC, then every run of whitespace
collapsed to one space, then stripped. Keying by voice + text is the safety
property: a clip only plays when it is exactly the text the agent would otherwise
have spoken through TTS, in the agent's own voice. Editing an agent's disclosure
or voice changes the key, so a stale clip is never served; the call falls back to
TTS until a new clip is rendered (scripts/generate_clips.py).

Where the clip enters: main.py still queues the disclosure as a ``TTSSpeakFrame``
at the pipeline head. ``DisclosureClipPlayer``, placed immediately before the TTS
service (downstream of STT), swaps that first matching frame for the clip. Never
queue the clip itself at the head: every ``AudioRawFrame`` is STT input, so Flux
would transcribe the bot's own disclosure as user speech.

The clip is a ``TTSAudioRawFrame``. Pipecat 1.12's output transport only emits
Bot{Started,Stopped}SpeakingFrame for TTS (or speech-stream) audio, not for a plain
``OutputAudioRawFrame``, and both DisclosureUserMuteStrategy and DisclosureGate key on
the first BotStoppedSpeakingFrame. The transport resamples
the clip to its own output rate (8 kHz on Telnyx), so clips keep their native rate.
"""
import hashlib
import logging
import unicodedata
from pathlib import Path

from pipecat.frames.frames import TTSAudioRawFrame, TTSSpeakFrame, TTSStoppedFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from pipesong.config import settings
from pipesong.prerecorded.wav import read_pcm16_mono

logger = logging.getLogger(__name__)


def normalize_disclosure_text(text: str) -> str:
    """NFC, collapse all whitespace runs to a single space, strip."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def disclosure_clip_path(base_dir: str | Path, voice: str, text: str) -> Path:
    """Path of the clip for ``voice`` + ``text``: ``<base_dir>/disclosure/<sha256[:16]>.wav``."""
    key = f"{voice.strip()}\n{normalize_disclosure_text(text)}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return Path(base_dir) / "disclosure" / f"{digest}.wav"


def load_disclosure_clip(base_dir: str | Path, voice: str, text: str) -> TTSAudioRawFrame | None:
    """Load the clip for ``voice`` + ``text`` as one audio frame, or None to fall back to TTS.

    Returns None (logged at INFO) when the file is missing, unreadable, not 16-bit
    PCM mono, or holds no audio; None (WARNING) when not at settings.prerecorded_sample_rate. A missing clip logs at WARNING when the disclosure
    dir exists: clips were rendered, just not for this voice + text.
    """
    path = disclosure_clip_path(base_dir, voice, text)
    if not path.is_file():
        level = logging.WARNING if path.parent.is_dir() else logging.INFO
        logger.log(level, "disclosure clip not found: %s", path)
        return None
    clip = read_pcm16_mono(path, "disclosure clip", settings.prerecorded_sample_rate)
    if clip is None:
        return None
    audio, sample_rate = clip
    return TTSAudioRawFrame(audio=audio, sample_rate=sample_rate, num_channels=1)


class DisclosureClipPlayer(FrameProcessor):
    """Plays the pre-recorded clip in place of the disclosure TTSSpeakFrame.

    Place immediately before the TTS service. The first downstream TTSSpeakFrame
    whose normalized text equals the disclosure becomes ``[clip, TTSStoppedFrame]``
    when a clip exists; otherwise it passes through to TTS. Every other frame passes
    through untouched.

    The TTSStoppedFrame ends the bot turn as soon as the clip drains, so the
    first-bot-complete unmute and DisclosureGate fire then, not after the output
    transport's 3 s idle fallback (BOT_VAD_STOP_FALLBACK_SECS).
    """

    def __init__(self, base_dir: str | Path, voice: str, text: str, **kwargs):
        super().__init__(**kwargs)
        self._text = normalize_disclosure_text(text)
        self._path = disclosure_clip_path(base_dir, voice, text)
        self._clip = load_disclosure_clip(base_dir, voice, text)
        self._done = False

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if (
            not self._done
            and direction == FrameDirection.DOWNSTREAM
            and isinstance(frame, TTSSpeakFrame)
            and normalize_disclosure_text(frame.text) == self._text
        ):
            self._done = True
            if self._clip is not None:
                logger.info("disclosure: pre-recorded clip %s", self._path)
                await self.push_frame(self._clip, direction)
                await self.push_frame(TTSStoppedFrame(), direction)
                return
            logger.info("disclosure: TTS fallback")

        await self.push_frame(frame, direction)
