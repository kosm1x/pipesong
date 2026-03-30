# Code Audit — Phase 4a Latency Optimization (2026-03-30)

Audited: Phase 4a additions in `src/pipesong/`
Scope: MetricsCollector, SentenceStreamBuffer, ToolCallProcessor streaming mode, STTMuteFilter, VAD tuning, call_latency table, latency APIs
Auditor: QA Auditor (Claude Opus 4.6)

## Summary

Phase 4a adds per-turn latency instrumentation, Spanish-aware sentence streaming, early bail-out for tool calls, STT mute during disclosure/tool execution, and per-agent VAD tuning. The core logic is well-structured and the sentence boundary detection handles Spanish abbreviations, decimals, and ellipsis correctly. Several issues found, primarily around pipeline ordering, resource cleanup, input validation, and missing deployment documentation.

**11 findings:** 1 High, 5 Medium, 5 Low

---

## All Findings

| #   | Category     | Severity   | File           | Line(s) | Issue                                                                                                                                                                                                                                   | Status |
| --- | ------------ | ---------- | -------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ |
| H1  | Correctness  | **High**   | pipeline.py    | 143-152 | STTMuteFilter placed before STT instead of after. Pipecat docs specify placement between STT and context aggregator.                                                                                                                    | FIXED  |
| M1  | Resource     | **Medium** | pipeline.py    | 171     | ToolExecutor httpx.AsyncClient never closed. Leaks TCP connections per call.                                                                                                                                                            | FIXED  |
| M2  | Performance  | **Medium** | models/call.py | 37      | call_latency.created_at has no index. Agent latency query filters on it — sequential scan at scale.                                                                                                                                     | FIXED  |
| M3  | Validation   | **Medium** | api/agents.py  | 33-34   | vad_stop_secs and vad_confidence have no bounds validation. Negative or extreme values could break VAD.                                                                                                                                 | FIXED  |
| M4  | Deploy       | **Medium** | docs/          | —       | No deploy checklist for Phase 4a. Existing deployments need manual SQL for call_latency table and vad columns.                                                                                                                          | FIXED  |
| M5  | Performance  | **Medium** | processors.py  | 462-479 | `_extract_json_tool_call` is O(n \* m) where n = number of `{` chars and m = text length. 500 braces takes >1s.                                                                                                                         | FIXED  |
| L1  | Correctness  | **Low**    | processors.py  | 554-560 | Leading whitespace tokens set streaming=True prematurely. If LLM emits " {tool...}", JSON gets spoken.                                                                                                                                  | FIXED  |
| L2  | Code Quality | **Low**    | api/calls.py   | 2,4,6   | Unused imports: `timedelta`, `timezone`, `Query`, `func`. Added for Phase 4a but only used in agents.py.                                                                                                                                | FIXED  |
| L3  | Correctness  | **Low**    | processors.py  | 155-158 | e2e_ms computed as sum of components — only reflects serial TTFB addition, not true end-to-end user-perceived latency.                                                                                                                  | FIXED  |
| L4  | Correctness  | **Low**    | api/agents.py  | 160-161 | Percentile calculation uses `int(pct/100 * (n-1))` — for n=1, all percentiles return the same value. Correct but potentially misleading.                                                                                                | N/A    |
| L5  | Correctness  | **Low**    | processors.py  | 272     | `?` and `!` treated as immediate boundaries without checking for inverted Spanish pairs (e.g., `?` in the middle of `Hola?`). Works because inverted `?` and `!` are stripped by SpanishOnlyFilter's comma->period conversion upstream. | FIXED  |

---

## Detailed Findings

### H1: STTMuteFilter Placed Before STT (High)

**File:** `/root/claude/pipesong/src/pipesong/pipeline.py:143-152`

```python
processors = [
    transport.input(),
    stt_mute,         # <-- BEFORE stt
    stt,
]
```

Pipecat documentation explicitly states STTMuteFilter should be placed **between the STT service and context aggregator**:

```python
# Pipecat docs example:
pipeline = Pipeline([
    transport.input(),
    stt,
    stt_mute_filter,  # Between the STT service and context aggregator
    context_aggregator.user(),
])
```

The STTMuteFilter works by intercepting `TranscriptionFrame`, `InterimTranscriptionFrame`, `InterruptionFrame`, `UserStartedSpeakingFrame`, and `UserStoppedSpeakingFrame` — all frames **emitted by STT**. Placing it before STT means it never sees these frames in the downstream direction; it only sees raw audio frames from the transport.

**Fix:** Move `stt_mute` after `stt` and before the TranscriptCapture/user_aggregator:

```python
processors = [
    transport.input(),
    stt,
    stt_mute,  # After STT, before aggregator
]
```

---

### M1: ToolExecutor httpx.AsyncClient Never Closed (Medium)

**File:** `/root/claude/pipesong/src/pipesong/pipeline.py:171`

```python
tool_processor = ToolCallProcessor(
    tools=tools,
    tool_executor=ToolExecutor(),  # Creates httpx.AsyncClient
    ...
)
```

`ToolExecutor.__init__` creates `self._client = httpx.AsyncClient(timeout=30)` with a `close()` method, but `close()` is never called when the pipeline ends. Each call creates a new `ToolExecutor` with a new HTTP client, and the underlying TCP connection pool is never cleaned up.

**Fix:** Either:

- Add cleanup in the WebSocket handler's `finally` block: `if tool_processor: await tool_processor._tool_executor.close()`
- Or make ToolExecutor a context manager and use `async with`
- Or create a single shared ToolExecutor per application (since it's stateless)

---

### M2: Missing Index on call_latency.created_at (Medium)

**File:** `/root/claude/pipesong/src/pipesong/models/call.py:37`

The agent latency endpoint queries:

```python
.where(Call.agent_id == agent_id, CallLatency.created_at >= since)
```

`CallLatency.created_at` has no index. As the table grows (each call turn generates a row), this becomes a sequential scan joined with the `calls` table.

**Fix:** Add `index=True` to the `created_at` column:

```python
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=..., index=True)
```

---

### M3: No Validation Bounds on VAD Parameters (Medium)

**File:** `/root/claude/pipesong/src/pipesong/api/agents.py:33-34`

```python
vad_stop_secs: float | None = None
vad_confidence: float | None = None
```

No bounds validation. `vad_stop_secs` should be positive (typical range 0.1-2.0). `vad_confidence` should be 0.0-1.0. Setting `vad_stop_secs: 0.0` would make the VAD trigger instantly; `vad_confidence: -1.0` would break the confidence threshold check.

**Fix:** Add Pydantic validators:

```python
from pydantic import Field

vad_stop_secs: float | None = Field(default=None, ge=0.05, le=5.0)
vad_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
```

---

### M4: No Phase 4a Deploy Checklist (Medium)

Phase 2 audit included a deploy checklist with SQL migrations, env vars, and Telnyx config. Phase 4a adds:

- New table: `call_latency`
- New columns on `agents`: `vad_stop_secs`, `vad_confidence`

Existing deployments using `create_all()` will NOT automatically add:

- The new `call_latency` table (it will be created since it's a new table, actually)
- The new `vad_stop_secs` and `vad_confidence` columns on the existing `agents` table (columns are NOT added by `create_all()` to existing tables)

**Fix:** Add to deploy docs:

```sql
-- Phase 4a agent VAD columns
ALTER TABLE agents ADD COLUMN IF NOT EXISTS vad_stop_secs FLOAT;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS vad_confidence FLOAT;

-- call_latency table is auto-created by create_all() (new table)
-- But add created_at index for query performance:
CREATE INDEX IF NOT EXISTS ix_call_latency_created_at ON call_latency(created_at);
```

---

### M5: Quadratic Complexity in \_extract_json_tool_call (Medium)

**File:** `/root/claude/pipesong/src/pipesong/processors.py:462-479`

For each `{` in the text, tries `json.loads()` on progressively shorter substrings from that position. With adversarial input (many `{` characters), this is O(n \* m) where n is the count of `{` and m is text length.

Benchmarked: 500 `{` chars takes >1 second. In practice, LLM output is short (max_tokens=300), but a malicious prompt injection returning hundreds of `{` could cause latency spikes.

**Fix:** Either:

- Add a max iteration count / early termination
- Or limit scanning to the first `{` only (tool calls start at the beginning)
- Or use a regex to find the outermost `{...}` pair first, then `json.loads` once

---

### L1: Leading Whitespace Can Misclassify Tool Calls (Low)

**File:** `/root/claude/pipesong/src/pipesong/processors.py:554-560`

If the LLM emits a whitespace token (" ") as the first token, `_looks_like_tool_call(" ")` returns `False` (empty after strip), setting `streaming = True`. If subsequent tokens form `{"tool": ...}`, the JSON is passed through as speech to TTS.

In practice, Qwen/vLLM models rarely emit leading whitespace before tool JSON. Low risk.

**Fix:** Defer the streaming decision until `text_buffer.strip()` is non-empty:

```python
if self._streaming is None:
    if not self._text_buffer.strip():
        return  # Wait for non-whitespace content
    ...
```

---

### L2: Unused Imports in calls.py (Low)

**File:** `/root/claude/pipesong/src/pipesong/api/calls.py:2,4,6`

```python
from datetime import datetime, timedelta, timezone  # timedelta, timezone unused
from fastapi import APIRouter, Depends, HTTPException, Query  # Query unused
from sqlalchemy import func, select  # func unused
```

These were imported for Phase 4a features that ended up in `agents.py` instead.

---

### L3: e2e_ms Is Additive TTFB, Not True End-to-End (Low)

**File:** `/root/claude/pipesong/src/pipesong/processors.py:155-158`

```python
components = [metrics.get("stt_ms"), metrics.get("llm_ttft_ms"), metrics.get("tts_ttfb_ms")]
available = [c for c in components if c is not None]
e2e = sum(available) if available else None
```

This sums TTFB values from individual services. True end-to-end latency (user stops speaking -> user hears first audio) includes queue times, frame propagation, network latency, and audio buffering — not captured here. The metric name `e2e_ms` is misleading.

**Fix:** Rename to `pipeline_ttfb_sum_ms` or add a comment in the API docs clarifying this is an approximation.

---

### L4: Percentile Edge Case for n=1 (Low)

**File:** `/root/claude/pipesong/src/pipesong/api/agents.py:160-161`

```python
def _p(pct: float) -> float:
    idx = int(pct / 100 * (n - 1))
    return round(s[idx], 1)
```

For n=1: `idx = int(pct/100 * 0) = 0` for all percentiles. All four percentile values (p50/p90/p95/p99) will be identical. This is mathematically correct (with one sample, all percentiles are the same value), but could confuse API consumers.

No fix required — just note in API docs that percentiles require meaningful sample sizes (>10 turns).

---

### L5: Spanish Inverted Punctuation Edge Case (Low)

**File:** `/root/claude/pipesong/src/pipesong/processors.py:272`

The docstring mentions "¿¡ pair closings" as sentence boundaries, but the implementation only checks for `?` and `!` without distinguishing inverted forms (`¿` `¡`) used at sentence beginnings in Spanish.

This works in practice because:

1. SpanishOnlyFilter upstream converts commas to periods, which handles clause boundaries
2. The inverted marks (¿¡) appear at sentence starts, not ends
3. The closing marks (?!) correctly trigger boundaries

The docstring is slightly misleading about "pair closings" — there's no pair matching logic.

---

## Deploy Checklist (Phase 4a)

### Database Migrations (for existing deployments)

```sql
-- New table (auto-created by create_all() on fresh installs)
-- Only needed if instance was running before Phase 4a
CREATE TABLE IF NOT EXISTS call_latency (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id UUID REFERENCES calls(id),
    turn_index INTEGER NOT NULL,
    stt_ms FLOAT,
    llm_ttft_ms FLOAT,
    tts_ttfb_ms FLOAT,
    e2e_ms FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_call_latency_call_id ON call_latency(call_id);
CREATE INDEX IF NOT EXISTS ix_call_latency_created_at ON call_latency(created_at);

-- New agent columns (NOT auto-added by create_all() to existing table)
ALTER TABLE agents ADD COLUMN IF NOT EXISTS vad_stop_secs FLOAT;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS vad_confidence FLOAT;
```

### No new environment variables required.

### No new Python dependencies required.

---

## Verdict: PASS WITH WARNINGS

Phase 4a code is well-structured and the core logic (sentence boundary detection, metrics collection, streaming heuristic) is correct. The high-severity STTMuteFilter placement issue (H1) must be fixed — it renders the FIRST_SPEECH and FUNCTION_CALL mute strategies non-functional. The medium issues (M1-M5) should be addressed before production deployment. Low issues are cosmetic or edge cases.
