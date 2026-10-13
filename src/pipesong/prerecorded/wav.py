"""Reader shared by the A.0 disclosure clips and the A.1 phrase corpus."""
import logging
import wave
from pathlib import Path

logger = logging.getLogger(__name__)


def read_pcm16_mono(path: str | Path, what: str, expected_rate: int) -> tuple[bytes, int] | None:
    """Return ``(audio, sample_rate)`` for a 16-bit PCM mono WAV at ``expected_rate``, or None.

    None (logged at INFO) when the file is unreadable, not 16-bit PCM mono, or holds no
    (or truncated) audio; None (logged at WARNING) when its rate is not ``expected_rate``.
    One rate for every clip: the output transport's stream resampler is shared per
    sender and raises ValueError when the input rate changes mid-call.
    ``what`` names the clip in the log line, e.g. "disclosure clip".
    """
    try:
        with wave.open(str(path), "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            audio = wf.readframes(n_frames)
    except (OSError, EOFError, wave.Error) as e:
        logger.info("%s unreadable: %s (%s)", what, path, e)
        return None
    if channels != 1 or sample_width != 2:
        logger.info(
            "%s rejected: %s (%d channel(s), %d-bit; need mono 16-bit PCM)",
            what, path, channels, sample_width * 8,
        )
        return None
    if sample_rate != expected_rate:
        logger.warning("%s rejected: %s (%d Hz; need %d Hz)", what, path, sample_rate, expected_rate)
        return None
    if not audio or len(audio) != n_frames * 2:
        logger.info("%s empty or truncated: %s", what, path)
        return None
    return audio, sample_rate
