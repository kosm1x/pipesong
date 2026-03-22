#!/usr/bin/env python3
"""LLM Function Calling Benchmark — Phase 0 Task 0.3

Tests function calling accuracy across 20 scenarios.
Measures: correct tool selection, argument accuracy, missing-info handling.

Usage:
    python llm_function_calling.py --model-name qwen-7b --base-url http://localhost:8000/v1
"""
import argparse
import json
import time
from pathlib import Path

from openai import OpenAI


def evaluate_tool_call(response_msg, expected):
    """Compare LLM's tool call against expected outcome."""
    tool_calls = getattr(response_msg, "tool_calls", None) or []

    if expected.get("expected_tool") == "none":
        # Model should NOT call a tool — should ask for info or handle differently
        if len(tool_calls) == 0:
            return {"correct_tool": True, "correct_args": True, "behavior": "correctly_withheld"}
        else:
            called = tool_calls[0].function.name
            return {"correct_tool": False, "correct_args": False, "behavior": f"incorrectly_called_{called}"}

    if len(tool_calls) == 0:
        # Model should have called a tool but didn't
        return {"correct_tool": False, "correct_args": False, "behavior": "no_tool_called"}

    call = tool_calls[0]
    tool_name = call.function.name
    try:
        tool_args = json.loads(call.function.arguments)
    except (json.JSONDecodeError, TypeError):
        tool_args = {}

    correct_tool = tool_name == expected.get("expected_tool", "")

    # Check args if expected_args provided
    expected_args = expected.get("expected_args", {})
    args_match = {}
    for key, expected_val in expected_args.items():
        actual_val = tool_args.get(key, "__MISSING__")
        # Fuzzy match: case-insensitive string comparison
        if isinstance(expected_val, str) and isinstance(actual_val, str):
            args_match[key] = expected_val.lower() in actual_val.lower() or actual_val.lower() in expected_val.lower()
        else:
            args_match[key] = str(actual_val) == str(expected_val)

    all_args_correct = all(args_match.values()) if args_match else True

    return {
        "correct_tool": correct_tool,
        "correct_args": all_args_correct,
        "called_tool": tool_name,
        "called_args": tool_args,
        "args_detail": args_match,
        "behavior": "tool_called",
    }


def run_benchmark(model_name: str, base_url: str, prompts_file: str, output_dir: str, use_native_tools: bool):
    client = OpenAI(base_url=base_url, api_key="not-needed")
    models = client.models.list()
    model_id = models.data[0].id
    print(f"Using model: {model_id}")
    print(f"Native tool calling: {use_native_tools}")

    scenarios = []
    with open(prompts_file) as f:
        for line in f:
            scenarios.append(json.loads(line.strip()))

    print(f"Loaded {len(scenarios)} scenarios")

    results = []
    for i, scenario in enumerate(scenarios):
        start = time.perf_counter()
        try:
            kwargs = {
                "model": model_id,
                "messages": scenario["messages"],
                "max_tokens": 300,
                "temperature": 0.3,  # Lower temp for tool calling accuracy
            }

            if use_native_tools:
                kwargs["tools"] = scenario["tools"]
                kwargs["tool_choice"] = "auto"

            else:
                # Prompt-based: inject tools into system message
                tools_desc = json.dumps(scenario["tools"], indent=2, ensure_ascii=False)
                tool_system = (
                    f"\n\nTienes acceso a las siguientes herramientas. "
                    f"Cuando necesites usar una, responde ÚNICAMENTE con un JSON "
                    f"con el formato: {{\"tool\": \"nombre\", \"arguments\": {{...}}}}\n"
                    f"Si no necesitas usar una herramienta, responde normalmente.\n\n"
                    f"Herramientas:\n{tools_desc}"
                )
                msgs = list(scenario["messages"])
                msgs[0] = {"role": "system", "content": msgs[0]["content"] + tool_system}
                kwargs["messages"] = msgs

            response = client.chat.completions.create(**kwargs)
            elapsed = time.perf_counter() - start
            msg = response.choices[0].message

            if use_native_tools:
                eval_result = evaluate_tool_call(msg, scenario)
            else:
                # Parse tool call from text response
                content = msg.content or ""
                eval_result = {"correct_tool": False, "correct_args": False, "behavior": "prompt_based", "raw_response": content[:500]}
                try:
                    # Try to find JSON in response
                    for candidate in [content, content.strip().strip("```json").strip("```")]:
                        parsed = json.loads(candidate)
                        if "tool" in parsed:
                            eval_result["called_tool"] = parsed["tool"]
                            eval_result["called_args"] = parsed.get("arguments", {})
                            eval_result["correct_tool"] = parsed["tool"] == scenario.get("expected_tool", "")
                            break
                except (json.JSONDecodeError, TypeError):
                    pass

            result = {
                "id": scenario["id"],
                "category": scenario["category"],
                "expected_tool": scenario.get("expected_tool"),
                "expected_behavior": scenario.get("expected_behavior"),
                **eval_result,
                "response_time_ms": round(elapsed * 1000),
                "response_text": (msg.content or "")[:300],
            }

            status = "PASS" if eval_result["correct_tool"] else "FAIL"
            tool_info = eval_result.get("called_tool", eval_result.get("behavior", "?"))
            print(f"  [{i+1:2d}/20] {status} | {scenario['category']:20s} | expected={scenario.get('expected_tool','none'):20s} | got={tool_info}")

        except Exception as e:
            elapsed = time.perf_counter() - start
            result = {
                "id": scenario["id"],
                "category": scenario["category"],
                "error": str(e),
                "response_time_ms": round(elapsed * 1000),
            }
            print(f"  [{i+1:2d}/20] ERROR | {scenario['category']:20s} | {e}")

        results.append(result)

    output_path = Path(output_dir) / f"llm_function_calling_{model_name}.jsonl"
    with open(output_path, "w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Summary
    valid = [r for r in results if "error" not in r]
    tool_correct = sum(1 for r in valid if r.get("correct_tool"))
    args_correct = sum(1 for r in valid if r.get("correct_args"))

    print(f"\n{'='*60}")
    print(f"Model: {model_name}")
    print(f"Tool selection accuracy: {tool_correct}/{len(valid)} ({100*tool_correct/len(valid):.0f}%)" if valid else "No results")
    print(f"Argument accuracy: {args_correct}/{len(valid)} ({100*args_correct/len(valid):.0f}%)" if valid else "")
    print(f"Results saved to: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--base-url", default="http://localhost:8000/v1")
    parser.add_argument("--prompts", default="../prompts/function_calling_20.jsonl")
    parser.add_argument("--output-dir", default="../results")
    parser.add_argument("--no-native-tools", action="store_true", help="Use prompt-based instead of native tool calling")
    args = parser.parse_args()
    run_benchmark(args.model_name, args.base_url, args.prompts, args.output_dir, not args.no_native_tools)
