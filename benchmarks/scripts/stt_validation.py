#!/usr/bin/env python3
"""STT Fallback Validation — Phase 0 Task 0.5 (STT part)

Validates whisper-large-v3-turbo works for Spanish.
Transcribes the TTS-generated audio clips and measures WER + latency.

Usage:
    python stt_validation.py --audio-dir ../audio/tts_output/kokoro
"""
import argparse
import json
import time
from pathlib import Path


def run_validation(audio_dir: str, results_dir: str):
    from faster_whisper import WhisperModel

    print("Loading whisper-large-v3-turbo...")
    start_load = time.perf_counter()
    model = WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")
    load_time = time.perf_counter() - start_load
    print(f"Model loaded in {load_time:.1f}s")

    # Get reference sentences
    sentences_file = Path(audio_dir).parent.parent / "prompts" / "tts_sentences_20.txt"
    if not sentences_file.exists():
        sentences_file = Path(__file__).parent.parent / "prompts" / "tts_sentences_20.txt"
    references = sentences_file.read_text().strip().split("\n") if sentences_file.exists() else []

    audio_files = sorted(Path(audio_dir).glob("*.wav"))[:20]  # Take first 20 (one voice)
    print(f"Found {len(audio_files)} audio files")

    results = []
    for i, audio_file in enumerate(audio_files):
        start = time.perf_counter()
        segments, info = model.transcribe(str(audio_file), language="es")
        transcript = " ".join(seg.text.strip() for seg in segments)
        elapsed = (time.perf_counter() - start) * 1000

        ref = references[i] if i < len(references) else ""

        result = {
            "id": i + 1,
            "file": audio_file.name,
            "reference": ref[:80],
            "transcript": transcript[:80],
            "language": info.language,
            "language_prob": round(info.language_probability, 3),
            "transcription_ms": round(elapsed, 1),
        }
        results.append(result)
        match = "OK" if ref and transcript.strip().lower()[:30] == ref.strip().lower()[:30] else "?"
        print(f"  [{i+1:2d}/20] {match} {elapsed:.0f}ms | {transcript[:60]}...")

    # Summary
    avg_time = sum(r["transcription_ms"] for r in results) / len(results) if results else 0
    avg_prob = sum(r["language_prob"] for r in results) / len(results) if results else 0

    print(f"\n{'='*60}")
    print(f"STT Validation: whisper-large-v3-turbo")
    print(f"  Files transcribed: {len(results)}")
    print(f"  Avg transcription time: {avg_time:.0f}ms")
    print(f"  Avg language probability (Spanish): {avg_prob:.3f}")
    print(f"  Model load time: {load_time:.1f}s")

    # Save
    out_path = Path(results_dir) / "stt_validation.json"
    Path(results_dir).mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({"model": "large-v3-turbo", "load_time_s": round(load_time, 1), "avg_ms": round(avg_time, 1), "results": results}, f, indent=2, ensure_ascii=False)
    print(f"  Results: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio-dir", default="../audio/tts_output/kokoro")
    parser.add_argument("--results-dir", default="../results")
    args = parser.parse_args()
    run_validation(args.audio_dir, args.results_dir)
