#!/usr/bin/env python3
"""TTS Benchmark — Phase 0 Tasks 0.7, 0.8, 0.9

Generates Spanish sentences with TTS engines, downsamples to phone quality,
and measures TTFB at different concurrency levels.

Usage:
    python tts_benchmark.py --engine kokoro --base-url http://localhost:8880
"""
import argparse
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import aiohttp


def generate_sync(base_url: str, engine: str, voice: str, sentences_file: str, output_dir: str):
    """Generate audio for all sentences synchronously and measure TTFB."""
    import requests

    sentences = Path(sentences_file).read_text().strip().split("\n")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    results = []
    for i, sentence in enumerate(sentences):
        start = time.perf_counter()

        if engine == "kokoro":
            resp = requests.post(
                f"{base_url}/v1/audio/speech",
                json={
                    "model": "kokoro",
                    "input": sentence,
                    "voice": voice,
                    "response_format": "wav",
                    "speed": 1.0,
                },
                stream=True,
            )
            # Measure TTFB
            first_chunk = None
            audio_data = b""
            for chunk in resp.iter_content(chunk_size=4096):
                if first_chunk is None:
                    first_chunk = time.perf_counter()
                audio_data += chunk

            ttfb = (first_chunk - start) * 1000 if first_chunk else None
            total = (time.perf_counter() - start) * 1000

        elif engine == "fish":
            # Fish Speech API
            resp = requests.post(
                f"{base_url}/v1/tts",
                json={
                    "text": sentence,
                    "reference_id": voice,
                    "format": "wav",
                },
                stream=True,
            )
            first_chunk = None
            audio_data = b""
            for chunk in resp.iter_content(chunk_size=4096):
                if first_chunk is None:
                    first_chunk = time.perf_counter()
                audio_data += chunk

            ttfb = (first_chunk - start) * 1000 if first_chunk else None
            total = (time.perf_counter() - start) * 1000

        else:
            raise ValueError(f"Unknown engine: {engine}")

        wav_path = output_path / f"{engine}_{voice}_{i+1:02d}.wav"
        with open(wav_path, "wb") as f:
            f.write(audio_data)

        result = {
            "id": i + 1,
            "engine": engine,
            "voice": voice,
            "sentence": sentence[:60],
            "ttfb_ms": round(ttfb, 1) if ttfb else None,
            "total_ms": round(total, 1),
            "audio_bytes": len(audio_data),
        }
        results.append(result)
        print(f"  [{i+1:2d}/20] TTFB={ttfb:.0f}ms total={total:.0f}ms size={len(audio_data)} | {sentence[:50]}...")

    return results


async def measure_concurrent_ttfb(base_url: str, engine: str, voice: str, sentences: list, concurrency: int):
    """Measure TTFB at given concurrency level."""
    async def single_request(session, sentence, idx):
        start = time.perf_counter()
        if engine == "kokoro":
            url = f"{base_url}/v1/audio/speech"
            payload = {"model": "kokoro", "input": sentence, "voice": voice, "response_format": "wav", "speed": 1.0}
        elif engine == "fish":
            url = f"{base_url}/v1/tts"
            payload = {"text": sentence, "reference_id": voice, "format": "wav"}
        else:
            return {"error": f"Unknown engine: {engine}"}

        try:
            async with session.post(url, json=payload) as resp:
                first_byte = None
                async for chunk in resp.content.iter_chunked(4096):
                    if first_byte is None:
                        first_byte = time.perf_counter()
                    # Don't need to store the full audio for latency test
                ttfb = (first_byte - start) * 1000 if first_byte else None
                total = (time.perf_counter() - start) * 1000
                return {"id": idx, "ttfb_ms": round(ttfb, 1) if ttfb else None, "total_ms": round(total, 1)}
        except Exception as e:
            return {"id": idx, "error": str(e)}

    connector = aiohttp.TCPConnector(limit=concurrency + 5)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [single_request(session, sentences[i % len(sentences)], i) for i in range(20)]
        # Run in batches of concurrency
        results = []
        for batch_start in range(0, len(tasks), concurrency):
            batch = tasks[batch_start:batch_start + concurrency]
            batch_results = await asyncio.gather(*batch)
            results.extend(batch_results)
        return results


def downsample_to_phone(input_dir: str, output_dir: str):
    """Downsample WAV files to 8kHz mono G.711 mulaw (phone codec)."""
    in_path = Path(input_dir)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    wav_files = sorted(in_path.glob("*.wav"))
    for wav in wav_files:
        out_file = out_path / f"phone_{wav.name}"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav), "-ar", "8000", "-ac", "1", "-acodec", "pcm_mulaw", str(out_file)],
            capture_output=True,
        )
    print(f"  Downsampled {len(wav_files)} files to {out_path}")


def percentile(values, p):
    if not values:
        return None
    s = sorted(values)
    idx = min(int(len(s) * p / 100), len(s) - 1)
    return s[idx]


def run_benchmark(engine: str, base_url: str, voice: str, sentences_file: str, output_base: str, results_dir: str):
    print(f"\n{'='*60}")
    print(f"TTS Benchmark: {engine} (voice: {voice})")
    print(f"{'='*60}")

    sentences = Path(sentences_file).read_text().strip().split("\n")

    # 1. Generate all 20 sentences
    print(f"\n--- Generation (sequential, measuring TTFB) ---")
    audio_dir = f"{output_base}/{engine}"
    gen_results = generate_sync(base_url, engine, voice, sentences_file, audio_dir)

    # 2. Downsample to phone quality
    print(f"\n--- Downsampling to 8kHz G.711 ---")
    phone_dir = f"{output_base}/../phone_quality"
    downsample_to_phone(audio_dir, phone_dir)

    # 3. Concurrent TTFB measurement
    print(f"\n--- Concurrent TTFB (concurrency=10) ---")
    concurrent_results = asyncio.run(measure_concurrent_ttfb(base_url, engine, voice, sentences, 10))
    concurrent_ttfbs = [r["ttfb_ms"] for r in concurrent_results if r.get("ttfb_ms")]

    # 4. Summary
    seq_ttfbs = [r["ttfb_ms"] for r in gen_results if r.get("ttfb_ms")]

    summary = {
        "engine": engine,
        "voice": voice,
        "sequential": {
            "ttfb_p50": round(percentile(seq_ttfbs, 50), 1) if seq_ttfbs else None,
            "ttfb_p90": round(percentile(seq_ttfbs, 90), 1) if seq_ttfbs else None,
            "ttfb_min": round(min(seq_ttfbs), 1) if seq_ttfbs else None,
            "ttfb_max": round(max(seq_ttfbs), 1) if seq_ttfbs else None,
        },
        "concurrent_10": {
            "ttfb_p50": round(percentile(concurrent_ttfbs, 50), 1) if concurrent_ttfbs else None,
            "ttfb_p90": round(percentile(concurrent_ttfbs, 90), 1) if concurrent_ttfbs else None,
            "ttfb_min": round(min(concurrent_ttfbs), 1) if concurrent_ttfbs else None,
            "ttfb_max": round(max(concurrent_ttfbs), 1) if concurrent_ttfbs else None,
        },
    }

    print(f"\n{'='*60}")
    print(f"SUMMARY: {engine} ({voice})")
    print(f"  Sequential TTFB:   p50={summary['sequential']['ttfb_p50']}ms  p90={summary['sequential']['ttfb_p90']}ms")
    print(f"  Concurrent(10) TTFB: p50={summary['concurrent_10']['ttfb_p50']}ms  p90={summary['concurrent_10']['ttfb_p90']}ms")
    print(f"  Audio files: {audio_dir}")
    print(f"  Phone quality: {phone_dir}")

    # Save results
    results_path = Path(results_dir)
    results_path.mkdir(parents=True, exist_ok=True)

    with open(results_path / f"tts_{engine}_{voice}.json", "w") as f:
        json.dump({"summary": summary, "generation": gen_results}, f, indent=2, ensure_ascii=False)

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, choices=["kokoro", "fish"])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--voice", required=True)
    parser.add_argument("--sentences", default="../prompts/tts_sentences_20.txt")
    parser.add_argument("--audio-dir", default="../audio/tts_output")
    parser.add_argument("--results-dir", default="../results")
    args = parser.parse_args()
    run_benchmark(args.engine, args.base_url, args.voice, args.sentences, args.audio_dir, args.results_dir)
