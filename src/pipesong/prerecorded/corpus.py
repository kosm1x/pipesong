"""Pre-recorded phrase corpus (roadmap A.1).

A clip lives at ``<base_dir>/corpus/<key>.wav`` with ``key = sha256(voice + "\\n" +
normalize_phrase(text))[:16]``, and is listed in ``<base_dir>/corpus/manifest.json``
(``[{"key", "voice", "text"}]``, written by scripts/generate_clips.py --corpus).
Only manifest entries for this voice whose key matches their text and whose clip is
a valid 16-bit mono WAV are served; anything else is skipped, so a stale or foreign
file can never play.

Matching is exact on the normalized text: a near-miss is a miss and goes to TTS.
The wrong clip is worse than a slow one (A.2 gate), so there is no fuzzy matching.
Normalization only removes what cannot change the words spoken: Unicode form, case,
punctuation (incl. ¿¡…) and whitespace; the sentence type (question / exclamation /
statement) stays in the key.

Clips are read once per ``Corpus.load()``, i.e. once per call (create_pipeline):
a corpus is a few seconds of audio per phrase (~1 MB for ten phrases), and loading
per call means a re-rendered corpus takes effect on the next call without a restart,
as A.0's disclosure clip does.
"""
import hashlib
import json
import logging
import unicodedata
from pathlib import Path

from pipecat.frames.frames import TTSAudioRawFrame

from pipesong.config import settings
from pipesong.prerecorded.wav import read_pcm16_mono

logger = logging.getLogger(__name__)


def normalize_phrase(text: str) -> str:
    """NFC + casefold, every punctuation char (Unicode category P*) to a space, collapse
    whitespace, then append the sentence type as a final token: " ?" if the text had
    ``?`` or ``¿`` (question wins), else " !" if it had ``!`` or ``¡``, else nothing.

    The sentence-type token keeps "¿Su cita ha quedado confirmada?" from playing the
    "Su cita ha quedado confirmada." clip: same words, opposite meaning.
    """
    text = unicodedata.normalize("NFC", text.casefold())
    kind = " ?" if ("?" in text or "¿" in text) else " !" if ("!" in text or "¡" in text) else ""
    text = "".join(" " if unicodedata.category(ch).startswith("P") else ch for ch in text)
    return " ".join(text.split()) + kind


def phrase_key(voice: str, text: str) -> str:
    """First 16 hex chars of ``sha256(voice.strip() + "\\n" + normalize_phrase(text))``."""
    raw = f"{voice.strip()}\n{normalize_phrase(text)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class Corpus:
    """Pre-rendered clips for one voice, looked up by exact normalized text."""

    def __init__(self, voice: str, clips: dict[str, tuple[bytes, int]] | None = None):
        self.voice = voice.strip()
        self._clips = clips or {}  # key -> (audio, sample_rate)

    def __len__(self) -> int:
        return len(self._clips)

    @classmethod
    def load(cls, base_dir: str | Path, voice: str) -> "Corpus":
        """Load ``<base_dir>/corpus/`` for ``voice``. Missing dir or manifest → empty corpus."""
        corpus_dir = Path(base_dir) / "corpus"
        manifest = corpus_dir / "manifest.json"
        if not manifest.is_file():
            logger.info("corpus: no manifest at %s, empty corpus", manifest)
            return cls(voice)
        try:
            entries = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                raise ValueError("manifest is not a list")
        except (OSError, ValueError) as e:
            logger.warning("corpus: unreadable manifest %s (%s), empty corpus", manifest, e)
            return cls(voice)

        voice = voice.strip()
        clips: dict[str, tuple[bytes, int]] = {}
        skipped = 0
        for entry in entries:
            if not isinstance(entry, dict) or str(entry.get("voice", "")).strip() != voice:
                continue
            key, text = entry.get("key"), entry.get("text")
            if not isinstance(text, str) or key != phrase_key(voice, text):
                logger.warning("corpus: manifest key does not match its text, skipped: %r", entry)
                skipped += 1
                continue
            path = corpus_dir / f"{key}.wav"
            clip = None
            if path.is_file():
                clip = read_pcm16_mono(path, "corpus clip", settings.prerecorded_sample_rate)
            if clip is None:
                skipped += 1
                continue
            clips[key] = clip
        logger.info(
            "corpus: %d clip(s) for voice %s from %s (%d manifest entr%s skipped)",
            len(clips), voice, corpus_dir, skipped, "y" if skipped == 1 else "ies",
        )
        return cls(voice, clips)

    def lookup(self, text: str) -> TTSAudioRawFrame | None:
        """A fresh audio frame for ``text`` on an exact normalized match, else None.

        Fresh per call: a phrase can play several times in one call, and pipecat
        frames carry per-instance ids and timestamps.
        """
        clip = self._clips.get(phrase_key(self.voice, text))
        if clip is None:
            return None
        audio, sample_rate = clip
        return TTSAudioRawFrame(audio=audio, sample_rate=sample_rate, num_channels=1)
