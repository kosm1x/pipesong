#!/usr/bin/env python3
"""LLM Conversational Quality Benchmark — Phase 0 Task 0.2

Sends 50 Spanish conversational prompts to a vLLM server and saves responses.
Run once per model (swap model by restarting vLLM with different --model).

Usage:
    python llm_conversational.py --model-name qwen-7b --base-url http://localhost:8000/v1
"""
import argparse
import json
import time
from pathlib import Path

from openai import OpenAI


def run_benchmark(model_name: str, base_url: str, prompts_file: str, output_dir: str):
    client = OpenAI(base_url=base_url, api_key="not-needed")

    # Discover the model ID from vLLM
    models = client.models.list()
    model_id = models.data[0].id
    print(f"Using model: {model_id}")

    prompts = []
    with open(prompts_file) as f:
        for line in f:
            prompts.append(json.loads(line.strip()))

    print(f"Loaded {len(prompts)} prompts")

    results = []
    for i, prompt in enumerate(prompts):
        start = time.perf_counter()
        try:
            response = client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "system", "content": prompt["system_prompt"]},
                    {"role": "user", "content": prompt["user_message"]},
                ],
                max_tokens=300,
                temperature=0.7,
            )
            elapsed = time.perf_counter() - start
            content = response.choices[0].message.content
            usage = response.usage

            result = {
                "id": prompt["id"],
                "category": prompt["category"],
                "user_message": prompt["user_message"],
                "response": content,
                "response_time_ms": round(elapsed * 1000),
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "finish_reason": response.choices[0].finish_reason,
            }
            print(f"  [{i+1:2d}/50] {prompt['category']:20s} | {elapsed:.1f}s | {usage.completion_tokens} tokens | {content[:60]}...")
        except Exception as e:
            elapsed = time.perf_counter() - start
            result = {
                "id": prompt["id"],
                "category": prompt["category"],
                "user_message": prompt["user_message"],
                "response": None,
                "error": str(e),
                "response_time_ms": round(elapsed * 1000),
            }
            print(f"  [{i+1:2d}/50] {prompt['category']:20s} | ERROR: {e}")

        results.append(result)

    output_path = Path(output_dir) / f"llm_conversational_{model_name}.jsonl"
    with open(output_path, "w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Summary
    successful = [r for r in results if r.get("response")]
    avg_time = sum(r["response_time_ms"] for r in successful) / len(successful) if successful else 0
    avg_tokens = sum(r["completion_tokens"] for r in successful) / len(successful) if successful else 0

    print(f"\n{'='*60}")
    print(f"Model: {model_name}")
    print(f"Successful: {len(successful)}/{len(results)}")
    print(f"Avg response time: {avg_time:.0f}ms")
    print(f"Avg completion tokens: {avg_tokens:.0f}")
    print(f"Results saved to: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-name", required=True, help="Short name for output file (e.g. qwen-7b)")
    parser.add_argument("--base-url", default="http://localhost:8000/v1")
    parser.add_argument("--prompts", default="../prompts/spanish_conversational_50.jsonl")
    parser.add_argument("--output-dir", default="../results")
    args = parser.parse_args()
    run_benchmark(args.model_name, args.base_url, args.prompts, args.output_dir)
