#!/usr/bin/env python3
"""Render pre-recorded clips offline: A.0 disclosure clips and A.1 phrase-corpus clips.

Disclosure mode (--text / --from-db) writes one 16-bit mono 24 kHz WAV per distinct
(voice, disclosure text) to disclosure_clip_path(<out>, voice, text), the path
DisclosureClipPlayer looks up at call start (src/pipesong/prerecorded/disclosure.py).
Corpus mode (--corpus FILE, one phrase per line, blank lines and # comments ignored)
writes <out>/corpus/<phrase_key(voice, phrase)>.wav and merges an entry per phrase
into <out>/corpus/manifest.json (sorted by key), which Corpus.load reads
(src/pipesong/prerecorded/corpus.py). --prune also removes this voice's entries and
clips for lines no longer in the file (otherwise a removed line keeps playing). A missing clip falls back to live TTS, so
rendering is optional and safe to re-run; existing clips are skipped unless --force.

Synthesis uses kokoro-onnx, the engine behind the live KokoroTTSService (already
installed via pipecat-ai[kokoro]): same model and voice files, same espeak-ng
phonemizer, same language code, so the clip sounds like the rest of the call. It
is CPU-only and imported lazily; the first run downloads ~340 MB of model files to
~/.cache/pipecat/kokoro-onnx (shared with the live service).

Usage (from the repo root):
  PYTHONPATH=src .venv/bin/python scripts/generate_clips.py --text "Esta llamada..."
  PYTHONPATH=src .venv/bin/python scripts/generate_clips.py --from-db [--force]
  PYTHONPATH=src .venv/bin/python scripts/generate_clips.py --corpus data/prerecorded/starter-corpus-es.txt --voice em_alex --prune
"""
import argparse
import asyncio
import functools
import json
import sys
import wave
from pathlib import Path

from pipesong.config import settings
from pipesong.prerecorded.corpus import phrase_key
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


@functools.cache  # one engine for both modes
def _load_kokoro():
    from kokoro_onnx import Kokoro
    from pipecat.services.kokoro.tts import KOKORO_CACHE_DIR, _ensure_model_files

    model_path = KOKORO_CACHE_DIR / "kokoro-v1.0.onnx"
    voices_path = KOKORO_CACHE_DIR / "voices-v1.0.bin"
    _ensure_model_files(model_path, voices_path)
    return Kokoro(str(model_path), str(voices_path))


def _write_wav(path: Path, samples, sample_rate: int) -> float:
    import numpy as np

    if sample_rate != settings.prerecorded_sample_rate:
        # Loaders reject any other rate (one resampler per output sender), so a clip at
        # another rate would be dead weight. kokoro-onnx renders 24 kHz.
        raise SystemExit(
            f"kokoro rendered {sample_rate} Hz but settings.prerecorded_sample_rate is "
            f"{settings.prerecorded_sample_rate} Hz; refusing to write {path}"
        )

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


def _corpus_phrases(path: str) -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def merge_manifest(entries: list[dict], voice: str, phrases: list[str], prune: bool) -> tuple[list[dict], list[dict]]:
    """Merge one entry per phrase for ``voice`` into ``entries``; return (manifest, pruned).

    Entries are keyed by ``key`` (a phrase already present is replaced, so re-runs are
    idempotent) and returned sorted by key for stable diffs. With ``prune``, entries for
    this voice that no phrase produces any more (removed lines, or keys from an older
    normalization) are dropped and returned as ``pruned``; other voices are never touched.
    """
    voice = voice.strip()
    by_key = {e["key"]: e for e in entries}
    for text in phrases:
        key = phrase_key(voice, text)
        by_key[key] = {"key": key, "voice": voice, "text": text}
    pruned = []
    if prune:
        keep = {phrase_key(voice, text) for text in phrases}
        pruned = [e for e in by_key.values() if str(e.get("voice", "")).strip() == voice and e["key"] not in keep]
        for e in pruned:
            del by_key[e["key"]]
    return sorted(by_key.values(), key=lambda e: e["key"]), sorted(pruned, key=lambda e: e["key"])


def _render_corpus(phrases: list[str], voice: str, lang: str, out: str, force: bool, prune: bool) -> None:
    corpus_dir = Path(out) / "corpus"
    manifest_path = corpus_dir / "manifest.json"
    entries = []
    if manifest_path.is_file():
        entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    voice = voice.strip()
    for text in phrases:
        if any(ch in text.rstrip(".!?") for ch in ",.!?"):
            # SpanishOnlyFilter turns commas into periods and SentenceStreamBuffer splits
            # there, so LLM output never reaches the mixer as one frame with this line.
            print(f"note  {text!r} spans several clauses: it can only match a TTSSpeakFrame "
                  "pushed whole (e.g. a tool filler), never LLM output")
        key = phrase_key(voice, text)
        path = corpus_dir / f"{key}.wav"
        if path.exists() and not force:
            print(f"skip  {path} (exists; --force to re-render)")
        else:
            samples, sample_rate = _load_kokoro().create(text, voice=voice, speed=1.0, lang=lang)
            duration = _write_wav(path, samples, sample_rate)
            print(f"wrote {path} {duration:.2f}s {sample_rate} Hz voice={voice} text={text!r}")
    manifest, pruned = merge_manifest(entries, voice, phrases, prune)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(manifest_path)
    for entry in pruned:
        (corpus_dir / f"{entry['key']}.wav").unlink(missing_ok=True)
        print(f"prune {corpus_dir / (entry['key'] + '.wav')} text={entry.get('text')!r}")
    print(f"manifest {manifest_path}: {len(manifest)} entr{'y' if len(manifest) == 1 else 'ies'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--text", action="append", default=[], help="disclosure text (repeatable)")
    parser.add_argument("--from-db", action="store_true", help="render every agent's disclosure + the config default")
    parser.add_argument("--corpus", help="phrase file (one per line, # comments) to render into <out>/corpus/")
    parser.add_argument("--prune", action="store_true",
                        help="with --corpus: drop this voice's manifest entries (and clips) not in the file")
    parser.add_argument("--voice", default="em_alex", help="Kokoro voice for --text, --corpus and the config default")
    parser.add_argument("--lang", default="es", help="kokoro-onnx language code (live TTS uses 'es')")
    parser.add_argument("--out", default=settings.prerecorded_dir, help="clip base dir (default: settings.prerecorded_dir)")
    parser.add_argument("--force", action="store_true", help="re-render clips that already exist")
    args = parser.parse_args()

    pairs = [(t, args.voice) for t in args.text]
    if args.from_db:
        pairs += asyncio.run(_pairs_from_db(args.voice))
    if not pairs and not args.corpus:
        parser.error("give --text, --from-db and/or --corpus")

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

    if args.corpus:
        _render_corpus(_corpus_phrases(args.corpus), args.voice, args.lang, args.out, args.force, args.prune)
    return 0


if __name__ == "__main__":
    sys.exit(main())
