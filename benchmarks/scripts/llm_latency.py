#!/usr/bin/env python3
"""LLM Latency Benchmark — Phase 0 Task 0.4

Measures time-to-first-token (TTFT) at various concurrency levels.
This is the most critical benchmark — determines the Groq overflow threshold.

Usage:
    python llm_latency.py --model-name qwen-7b --base-url http://localhost:8000
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

import aiohttp

CONCURRENCY_LEVELS = [1, 5, 10, 15, 20]
REQUESTS_PER_LEVEL = 30

# Reuse a set of varied prompts to avoid caching effects
PROMPTS = [
    ("Eres un agente de soporte técnico amable.", "Mi internet no funciona desde ayer."),
    ("Eres un agente de reservaciones.", "Quiero hacer una cita para el martes."),
    ("Eres un agente de ventas.", "¿Cuánto cuesta el plan premium?"),
    ("Eres un agente de soporte técnico.", "La aplicación se cierra sola cada vez que la abro."),
    ("Eres un agente bancario.", "Necesito información sobre mi estado de cuenta."),
    ("Eres un agente de servicio al cliente.", "Quiero devolver un producto que compré."),
    ("Eres un agente de soporte.", "No puedo conectar mi impresora a la red WiFi."),
    ("Eres un agente de citas médicas.", "Mi hijo tiene fiebre, ¿tienen espacio hoy?"),
    ("Eres un agente de telecomunicaciones.", "Quiero cambiar mi plan a uno con más datos."),
    ("Eres un agente de seguros.", "Tuve un accidente menor, ¿cómo hago el reclamo?"),
]


async def measure_single_request(session, base_url, model_id, prompt_idx, request_id):
    """Send a streaming request and measure TTFT."""
    system, user = PROMPTS[prompt_idx % len(PROMPTS)]
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 150,
        "temperature": 0.7,
        "stream": True,
    }

    start = time.perf_counter()
    ttft = None
    total_chunks = 0
    total_content = ""

    try:
        async with session.post(
            f"{base_url}/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as resp:
            async for line in resp.content:
                decoded = line.decode("utf-8").strip()
                if not decoded or not decoded.startswith("data: "):
                    continue
                data_str = decoded[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content and ttft is None:
                        ttft = (time.perf_counter() - start) * 1000  # ms
                    if content:
                        total_content += content
                        total_chunks += 1
                except (json.JSONDecodeError, KeyError, IndexError):
                    pass

        total_time = (time.perf_counter() - start) * 1000
        return {
            "request_id": request_id,
            "ttft_ms": round(ttft, 1) if ttft else None,
            "total_ms": round(total_time, 1),
            "chunks": total_chunks,
            "output_chars": len(total_content),
            "error": None,
        }
    except Exception as e:
        total_time = (time.perf_counter() - start) * 1000
        return {
            "request_id": request_id,
            "ttft_ms": None,
            "total_ms": round(total_time, 1),
            "chunks": 0,
            "output_chars": 0,
            "error": str(e),
        }


async def run_concurrency_level(base_url, model_id, concurrency, requests_count):
    """Run N concurrent requests and collect TTFT measurements."""
    connector = aiohttp.TCPConnector(limit=concurrency + 5)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            measure_single_request(session, base_url, model_id, i, i)
            for i in range(requests_count)
        ]

        # Launch in batches of `concurrency`
        results = []
        for batch_start in range(0, len(tasks), concurrency):
            batch = tasks[batch_start:batch_start + concurrency]
            batch_results = await asyncio.gather(*batch)
            results.extend(batch_results)

        return results


def percentile(values, p):
    """Calculate percentile from a sorted list."""
    if not values:
        return None
    sorted_v = sorted(values)
    idx = int(len(sorted_v) * p / 100)
    idx = min(idx, len(sorted_v) - 1)
    return sorted_v[idx]


async def run_benchmark(model_name: str, base_url: str, output_dir: str):
    # Discover model ID
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{base_url}/v1/models") as resp:
            data = await resp.json()
            model_id = data["data"][0]["id"]
    print(f"Using model: {model_id}")

    all_results = []
    summary_rows = []

    for concurrency in CONCURRENCY_LEVELS:
        print(f"\n--- Concurrency: {concurrency} ({REQUESTS_PER_LEVEL} requests) ---")

        results = await run_concurrency_level(base_url, model_id, concurrency, REQUESTS_PER_LEVEL)

        ttfts = [r["ttft_ms"] for r in results if r["ttft_ms"] is not None]
        errors = sum(1 for r in results if r["error"])

        if ttfts:
            p50 = percentile(ttfts, 50)
            p90 = percentile(ttfts, 90)
            p95 = percentile(ttfts, 95)
            p99 = percentile(ttfts, 99)
            print(f"  TTFT p50={p50:.0f}ms  p90={p90:.0f}ms  p95={p95:.0f}ms  p99={p99:.0f}ms  errors={errors}")

            summary_rows.append({
                "concurrency": concurrency,
                "requests": REQUESTS_PER_LEVEL,
                "ttft_p50": round(p50, 1),
                "ttft_p90": round(p90, 1),
                "ttft_p95": round(p95, 1),
                "ttft_p99": round(p99, 1),
                "ttft_min": round(min(ttfts), 1),
                "ttft_max": round(max(ttfts), 1),
                "errors": errors,
            })
        else:
            print(f"  No successful requests! Errors: {errors}")

        for r in results:
            r["concurrency"] = concurrency
            r["model"] = model_name
            all_results.append(r)

    # Save detailed results
    detail_path = Path(output_dir) / f"llm_latency_{model_name}.jsonl"
    with open(detail_path, "w") as f:
        for r in all_results:
            f.write(json.dumps(r) + "\n")

    # Save summary
    summary_path = Path(output_dir) / f"llm_latency_{model_name}_summary.json"
    with open(summary_path, "w") as f:
        json.dump({"model": model_name, "levels": summary_rows}, f, indent=2)

    # Print summary table
    print(f"\n{'='*70}")
    print(f"LATENCY SUMMARY: {model_name}")
    print(f"{'='*70}")
    print(f"{'Concurrency':>12} {'p50':>8} {'p90':>8} {'p95':>8} {'p99':>8} {'Errors':>8}")
    print(f"{'-'*12:>12} {'-'*8:>8} {'-'*8:>8} {'-'*8:>8} {'-'*8:>8} {'-'*8:>8}")
    for row in summary_rows:
        print(f"{row['concurrency']:>12} {row['ttft_p50']:>7.0f}ms {row['ttft_p90']:>7.0f}ms {row['ttft_p95']:>7.0f}ms {row['ttft_p99']:>7.0f}ms {row['errors']:>8}")

    # Key finding
    if summary_rows:
        threshold = None
        for row in summary_rows:
            if row["ttft_p50"] > 500:
                threshold = row["concurrency"]
                break
        if threshold:
            print(f"\n⚠ TTFT p50 exceeds 500ms at concurrency={threshold} → Groq overflow threshold")
        else:
            print(f"\n✓ TTFT p50 stays under 500ms at all tested concurrency levels")

    print(f"\nDetailed results: {detail_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output-dir", default="../results")
    args = parser.parse_args()
    asyncio.run(run_benchmark(args.model_name, args.base_url, args.output_dir))
