#!/usr/bin/env python3
"""TTS Benchmark for XTTS-v2 and Fish Speech — Phase 0

Generates 20 Spanish sentences with both engines and measures TTFB.
Uses default Spanish voices (no cloning in this benchmark).

Usage:
    python tts_xtts_fish_benchmark.py --engine xtts
    python tts_xtts_fish_benchmark.py --engine fish
"""
import argparse
import json
import subprocess
import time
from pathlib import Path


def benchmark_xtts(sentences: list, output_dir: str):
    """Benchmark XTTS-v2 using the TTS Python package."""
    from TTS.api import TTS

    print("Loading XTTS-v2 model (this may download ~2GB on first run)...")
    start_load = time.perf_counter()
    tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=True)
    load_time = time.perf_counter() - start_load
    print(f"Model loaded in {load_time:.1f}s")

    # List available speakers
    if hasattr(tts, 'speakers') and tts.speakers:
        print(f"Available speakers: {tts.speakers[:10]}...")

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    results = []
    for i, sentence in enumerate(sentences):
        wav_path = str(out_path / f"xtts_es_{i+1:02d}.wav")
        start = time.perf_counter()
        try:
            tts.tts_to_file(
                text=sentence,
                file_path=wav_path,
                language="es",
                speaker=tts.speakers[0] if tts.speakers else None,
            )
            elapsed = (time.perf_counter() - start) * 1000
            size = Path(wav_path).stat().st_size
            result = {
                "id": i + 1,
                "engine": "xtts",
                "sentence": sentence[:60],
                "total_ms": round(elapsed, 1),
                "audio_bytes": size,
            }
            print(f"  [{i+1:2d}/20] {elapsed:.0f}ms | {size:>8} bytes | {sentence[:50]}...")
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            result = {"id": i + 1, "engine": "xtts", "error": str(e), "total_ms": round(elapsed, 1)}
            print(f"  [{i+1:2d}/20] ERROR: {e}")
        results.append(result)

    return results, load_time


def benchmark_fish(sentences: list, output_dir: str):
    """Benchmark Fish Speech using the fish_speech package."""
    print("Loading Fish Speech model...")
    start_load = time.perf_counter()

    try:
        from fish_speech.inference import generate_speech
        load_time = time.perf_counter() - start_load
        print(f"Fish Speech loaded in {load_time:.1f}s")
    except ImportError:
        # Try the CLI approach instead
        print("Direct import failed, using CLI approach...")
        load_time = 0

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    results = []
    for i, sentence in enumerate(sentences):
        wav_path = str(out_path / f"fish_es_{i+1:02d}.wav")
        start = time.perf_counter()
        try:
            # Use fish_speech CLI
            proc = subprocess.run(
                [
                    "python", "-m", "fish_speech.infer",
                    "--text", sentence,
                    "--output", wav_path,
                    "--checkpoint", str(Path.home() / "pipesong-benchmarks/models/fish-speech-1.5"),
                ],
                capture_output=True, text=True, timeout=60,
            )
            elapsed = (time.perf_counter() - start) * 1000
            if Path(wav_path).exists():
                size = Path(wav_path).stat().st_size
                result = {"id": i + 1, "engine": "fish", "sentence": sentence[:60], "total_ms": round(elapsed, 1), "audio_bytes": size}
                print(f"  [{i+1:2d}/20] {elapsed:.0f}ms | {size:>8} bytes | {sentence[:50]}...")
            else:
                result = {"id": i + 1, "engine": "fish", "error": proc.stderr[:200], "total_ms": round(elapsed, 1)}
                print(f"  [{i+1:2d}/20] ERROR: {proc.stderr[:100]}")
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            result = {"id": i + 1, "engine": "fish", "error": str(e), "total_ms": round(elapsed, 1)}
            print(f"  [{i+1:2d}/20] ERROR: {e}")
        results.append(result)

    return results, load_time


def downsample_to_phone(input_dir: str, output_dir: str, prefix: str):
    """Downsample to 8kHz G.711 mulaw."""
    in_path = Path(input_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    count = 0
    for wav in sorted(in_path.glob("*.wav")):
        out_file = out_path / f"phone_{wav.name}"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav), "-ar", "8000", "-ac", "1", "-acodec", "pcm_mulaw", str(out_file)],
            capture_output=True,
        )
        count += 1
    print(f"  Downsampled {count} files → {out_path}")


def run(engine: str, sentences_file: str, output_base: str, results_dir: str):
    sentences = Path(sentences_file).read_text().strip().split("\n")
    print(f"\n{'='*60}")
    print(f"TTS Benchmark: {engine}")
    print(f"{'='*60}\n")

    audio_dir = f"{output_base}/{engine}"

    if engine == "xtts":
        results, load_time = benchmark_xtts(sentences, audio_dir)
    elif engine == "fish":
        results, load_time = benchmark_fish(sentences, audio_dir)
    else:
        raise ValueError(f"Unknown engine: {engine}")

    # Downsample
    print(f"\n--- Downsampling to 8kHz G.711 ---")
    downsample_to_phone(audio_dir, f"{output_base}/../phone_quality", engine)

    # Summary
    successful = [r for r in results if "error" not in r]
    times = [r["total_ms"] for r in successful]
    if times:
        times.sort()
        p50 = times[len(times) // 2]
        p90 = times[int(len(times) * 0.9)]
        print(f"\n{'='*60}")
        print(f"SUMMARY: {engine}")
        print(f"  Successful: {len(successful)}/{len(results)}")
        print(f"  Generation time p50: {p50:.0f}ms  p90: {p90:.0f}ms")
        print(f"  Model load time: {load_time:.1f}s")

    # Save
    Path(results_dir).mkdir(parents=True, exist_ok=True)
    with open(f"{results_dir}/tts_{engine}.json", "w") as f:
        json.dump({"engine": engine, "load_time": round(load_time, 1), "results": results}, f, indent=2, ensure_ascii=False)
    print(f"  Results: {results_dir}/tts_{engine}.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=["xtts", "fish"])
    parser.add_argument("--sentences", default="../prompts/tts_sentences_20.txt")
    parser.add_argument("--audio-dir", default="../audio/tts_output")
    parser.add_argument("--results-dir", default="../results")
    args = parser.parse_args()
    run(args.engine, args.sentences, args.audio_dir, args.results_dir)
