#!/usr/bin/env python3
"""Render pre-recorded recording-disclosure clips offline (roadmap A.0).

Writes one 16-bit mono 24 kHz WAV per distinct (voice, disclosure text) to
disclosure_clip_path(<out>, voice, text), the path DisclosureClipPlayer looks up at
call start (src/pipesong/prerecorded/disclosure.py). A call whose clip is missing falls back
to live TTS, so rendering is optional and safe to re-run.

Synthesis uses kokoro-onnx, the engine behind the live KokoroTTSService (already
installed via pipecat-ai[kokoro]): same model and voice files, same espeak-ng
phonemizer, same language code, so the clip sounds like the rest of the call. It
is CPU-only and imported lazily; the first run downloads ~340 MB of model files to
~/.cache/pipecat/kokoro-onnx (shared with the live service).

Usage (from the repo root):
  PYTHONPATH=src .venv/bin/python scripts/generate_disclosure_clips.py --text "Esta llamada..."
  PYTHONPATH=src .venv/bin/python scripts/generate_disclosure_clips.py --from-db [--force]
"""
import argparse
import asyncio
import sys
import wave
from pathlib import Path

from pipesong.config import settings
from pipesong.prerecorded.disclosure import disclosure_clip_path, normalize_disclosure_text


async def _pairs_from_db(default_voice: str) -> list[tuple[str, str]]:
    """Distinct (disclosure text, voice) as main.py would resolve them, plus the config default."""
    from sqlalchemy import select

    from pipesong.models.agent import Agent
    from pipesong.services.database import async_session, engine

    try:
        async with async_session() as session:
            rows = (await session.execute(select(Agent.disclosure_message, Agent.voice).distinct())).all()
    finally:
        await engine.dispose()
    # main.py: disclosure_text = agent_disclosure or settings.disclosure_text
    pairs = [(msg or settings.disclosure_text, voice or default_voice) for msg, voice in rows]
    pairs.append((settings.disclosure_text, default_voice))
    return pairs


def _load_kokoro():
    from kokoro_onnx import Kokoro
    from pipecat.services.kokoro.tts import KOKORO_CACHE_DIR, _ensure_model_files

    model_path = KOKORO_CACHE_DIR / "kokoro-v1.0.onnx"
    voices_path = KOKORO_CACHE_DIR / "voices-v1.0.bin"
    _ensure_model_files(model_path, voices_path)
    return Kokoro(str(model_path), str(voices_path))


def _write_wav(path: Path, samples, sample_rate: int) -> float:
    import numpy as np

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".wav.tmp")
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    tmp.replace(path)  # never leave a half-written clip at the served path
    return len(pcm) / 2 / sample_rate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--text", action="append", default=[], help="disclosure text (repeatable)")
    parser.add_argument("--from-db", action="store_true", help="render every agent's disclosure + the config default")
    parser.add_argument("--voice", default="em_alex", help="Kokoro voice for --text and the config default")
    parser.add_argument("--lang", default="es", help="kokoro-onnx language code (live TTS uses 'es')")
    parser.add_argument("--out", default=settings.prerecorded_dir, help="clip base dir (default: settings.prerecorded_dir)")
    parser.add_argument("--force", action="store_true", help="re-render clips that already exist")
    args = parser.parse_args()

    pairs = [(t, args.voice) for t in args.text]
    if args.from_db:
        pairs += asyncio.run(_pairs_from_db(args.voice))
    if not pairs:
        parser.error("give --text and/or --from-db")

    # One clip per (voice, normalized text) — the clip key.
    jobs = {(voice.strip(), normalize_disclosure_text(text)) for text, voice in pairs}

    kokoro = None
    for voice, text in sorted(jobs):
        if not text:
            continue
        path = disclosure_clip_path(args.out, voice, text)
        if path.exists() and not args.force:
            print(f"skip  {path} (exists; --force to re-render)")
            continue
        if kokoro is None:
            kokoro = _load_kokoro()
        samples, sample_rate = kokoro.create(text, voice=voice, speed=1.0, lang=args.lang)
        duration = _write_wav(path, samples, sample_rate)
        print(f"wrote {path} {duration:.2f}s {sample_rate} Hz voice={voice} text={text[:60]!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
