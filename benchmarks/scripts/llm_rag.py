#!/usr/bin/env python3
"""LLM RAG Grounding Benchmark — Phase 0 Task 0.6

Tests hallucination rate: 20 questions with context chunks.
10 answerable, 5 partially answerable, 5 unanswerable.

Usage:
    python llm_rag.py --model-name qwen-7b --base-url http://localhost:8000/v1
"""
import argparse
import json
import time
from pathlib import Path

from openai import OpenAI

RAG_SYSTEM_PROMPT = """Eres un agente de atención al cliente. Responde ÚNICAMENTE con la información proporcionada en el contexto.

Reglas estrictas:
- Si la respuesta está en el contexto, responde de forma clara y directa.
- Si la respuesta está parcialmente en el contexto, responde lo que puedas y menciona explícitamente qué información no tienes.
- Si la respuesta NO está en el contexto, di claramente que no tienes esa información y ofrece alternativas (transferir la llamada, buscar en otro lado, etc.).
- NUNCA inventes información que no esté en el contexto.

Contexto:
{context}"""


def run_benchmark(model_name: str, base_url: str, prompts_file: str, output_dir: str):
    client = OpenAI(base_url=base_url, api_key="not-needed")
    models = client.models.list()
    model_id = models.data[0].id
    print(f"Using model: {model_id}")

    questions = []
    with open(prompts_file) as f:
        for line in f:
            questions.append(json.loads(line.strip()))

    print(f"Loaded {len(questions)} questions")

    results = []
    for i, q in enumerate(questions):
        context = "\n\n".join(f"- {chunk}" for chunk in q["context"])
        system = RAG_SYSTEM_PROMPT.format(context=context)

        start = time.perf_counter()
        try:
            response = client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": q["question"]},
                ],
                max_tokens=300,
                temperature=0.3,
            )
            elapsed = time.perf_counter() - start
            content = response.choices[0].message.content

            result = {
                "id": q["id"],
                "type": q["type"],
                "question": q["question"],
                "response": content,
                "response_time_ms": round(elapsed * 1000),
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            }
            print(f"  [{i+1:2d}/20] {q['type']:12s} | {elapsed:.1f}s | {content[:80]}...")

        except Exception as e:
            elapsed = time.perf_counter() - start
            result = {
                "id": q["id"],
                "type": q["type"],
                "question": q["question"],
                "error": str(e),
                "response_time_ms": round(elapsed * 1000),
            }
            print(f"  [{i+1:2d}/20] {q['type']:12s} | ERROR: {e}")

        results.append(result)

    output_path = Path(output_dir) / f"llm_rag_{model_name}.jsonl"
    with open(output_path, "w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Summary
    by_type = {}
    for r in results:
        t = r["type"]
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(r)

    print(f"\n{'='*60}")
    print(f"Model: {model_name}")
    for t, items in by_type.items():
        successful = [r for r in items if r.get("response")]
        avg_time = sum(r["response_time_ms"] for r in successful) / len(successful) if successful else 0
        print(f"  {t}: {len(successful)}/{len(items)} successful, avg {avg_time:.0f}ms")
    print(f"Results saved to: {output_path}")
    print(f"\nMANUAL REVIEW NEEDED:")
    print(f"  - For 'answerable': rate grounding 1-5 (is answer from context?)")
    print(f"  - For 'unanswerable': did model refuse or hallucinate?")
    print(f"  - For 'partial': did model acknowledge gaps?")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--base-url", default="http://localhost:8000/v1")
    parser.add_argument("--prompts", default="../prompts/rag_grounded_20.jsonl")
    parser.add_argument("--output-dir", default="../results")
    args = parser.parse_args()
    run_benchmark(args.model_name, args.base_url, args.prompts, args.output_dir)
