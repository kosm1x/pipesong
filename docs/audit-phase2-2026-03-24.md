# Code Audit — Phase 2 (2026-03-24)

Audited: all Phase 2 files in `src/pipesong/`
Focus: reliability, performance, speed

## Summary

Phase 2 adds prompt-based tool calling, webhooks, outbound calls, and agent model expansion. The core pipeline works — tool calling, end_call, webhooks, and outbound calls all verified with live phone calls. However, several reliability and security issues need attention before production use.

**14 findings:** 5 Critical, 6 High, 3 Medium, 5 Low

## All Findings

| #   | Category     | Severity     | File              | Issue                                                                                                                                                                                      | Status                                                                                                                                  |
| --- | ------------ | ------------ | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| C1  | Reliability  | **Critical** | api/outbound.py   | `pending_outbound` dict is in-memory, grows unbounded, lost on restart. Calls that answer after restart get no streaming.                                                                  | **RESOLVED** — `call_control_id` stored in Call DB model, webhook handler queries DB                                                    |
| C2  | Reliability  | **Critical** | processors.py     | `TOOL_CALL_RE` regex uses `[^}]*` for arguments — fails on nested JSON (e.g. `{"data": {"key": "val"}}`).                                                                                  | **RESOLVED** — replaced with `_extract_json_tool_call()` using progressive `json.loads`                                                 |
| C3  | Security     | **Critical** | api/agents.py     | `webhook_secret` returned in `AgentResponse` — API exposes secrets in plaintext on GET requests.                                                                                           | **RESOLVED** — excluded from `AgentResponse` model                                                                                      |
| C4  | Security     | **Critical** | All API files     | No API authentication on any endpoint. Anyone can create agents, initiate outbound calls, read transcripts.                                                                                | **RESOLVED** — Bearer token middleware, skips /health + /ws + /telnyx/webhook                                                           |
| C5  | Security     | **Critical** | api/telnyx.py     | No Telnyx webhook signature verification. Forged `call.answered` events could trigger `streaming_start` to arbitrary URLs.                                                                 | **RESOLVED** — shared secret token verified via query param. Set TELNYX_WEBHOOK_SECRET + update Telnyx webhook URL to include `?token=` |
| H1  | Reliability  | **High**     | main.py           | `max_call_duration` stored in agent model but never enforced. Calls can run indefinitely, consuming GPU/Deepgram resources.                                                                | `OPEN`                                                                                                                                  |
| H2  | Reliability  | **High**     | main.py           | `asyncio.create_task` for webhook delivery — tasks are not tracked. At high volume, unfinished tasks accumulate.                                                                           | `OPEN`                                                                                                                                  |
| H3  | Performance  | **High**     | services/tools.py | `httpx.AsyncClient` created per tool call (new TCP connection each time). Should use a shared client or connection pool.                                                                   | `OPEN`                                                                                                                                  |
| H4  | Performance  | **High**     | processors.py     | LLM context grows unbounded during tool-heavy calls — 2 messages added per tool invocation (assistant tool call + user result). Long calls with many tools will hit context window limits. | `OPEN`                                                                                                                                  |
| H5  | Security     | **High**     | processors.py     | Tool results injected as `"role": "user"` messages. External HTTP endpoints returning malicious content could manipulate LLM behavior (prompt injection via tool results).                 | `OPEN`                                                                                                                                  |
| H6  | Reliability  | **High**     | services/tools.py | `_substitute()` does double-pass replacement — `{{key}}` then `{key}`. If a variable VALUE contains `{another_key}`, the second pass expands it, causing unintended substitution.          | `OPEN`                                                                                                                                  |
| M1  | Reliability  | **Medium**   | api/telnyx.py     | `streaming_start` response status not checked properly. If Telnyx returns 4xx, outbound call connects but has no audio — user hears silence with no error feedback.                        | `OPEN`                                                                                                                                  |
| M2  | Performance  | **Medium**   | main.py           | Outbound calls queue disclosure `TTSSpeakFrame` immediately. If the WebSocket stream isn't fully ready, disclosure audio may be lost or arrive late.                                       | `OPEN`                                                                                                                                  |
| M3  | Performance  | **Medium**   | pipeline.py       | When no tools configured, assistant TranscriptCapture still placed after (nonexistent) ToolCallProcessor position. Harmless but asymmetric with user capture.                              | `OPEN`                                                                                                                                  |
| L1  | Code Quality | **Low**      | main.py           | Inline imports inside WebSocket handler (`from pipecat...`, `from sqlalchemy...`). Should be at module level.                                                                              | `OPEN`                                                                                                                                  |
| L2  | Code Quality | **Low**      | main.py           | `agent.name` used on line 143 after SQLAlchemy session closes. Works because `expire_on_commit=False` but is fragile — relies on implementation detail.                                    | `OPEN`                                                                                                                                  |
| L3  | Correctness  | **Low**      | processors.py     | `time.time()` used for `timestamp_ms`. For ordering within a call, `time.monotonic()` would be more reliable (immune to clock adjustments).                                                | `OPEN`                                                                                                                                  |
| L4  | Reliability  | **Low**      | processors.py     | `TOOL_CALL_NATIVE_RE` matches any `[a-z_]+` before `{...}`. Could false-positive on text like `información{"key": "val"}` (unlikely but possible).                                         | `OPEN`                                                                                                                                  |
| L5  | Quality      | **Low**      | Project-wide      | Zero test coverage. No test files exist.                                                                                                                                                   | `OPEN` — Phase 2 was rapid prototyping                                                                                                  |

## Recommended Fix Priority

### Before Production (Phase 6)

- **C3**: Exclude `webhook_secret` from `AgentResponse` (use `model_fields` exclude or separate response model)
- **C4**: Add API key authentication middleware
- **C5**: Verify Telnyx webhook signatures
- **H5**: Sanitize tool results before injecting into LLM context (strip control characters, limit length)
- **H6**: Fix `_substitute` to only do one-pass replacement with `{{key}}` format

### Before Scale Testing (Phase 4-5)

- **C1**: Replace `pending_outbound` dict with Redis or DB-backed lookup with TTL
- **C2**: Fix regex to handle nested JSON (use `json.loads` on the full response with progressive parsing, not regex)
- **H1**: Enforce `max_call_duration` with `asyncio.wait_for` or timer task
- **H3**: Share `httpx.AsyncClient` across tool calls (create in `ToolExecutor.__init__`, close on shutdown)
- **H4**: Cap LLM context at N messages, trim oldest when exceeded

### Nice to Have

- **H2**: Track webhook tasks with a set, log if > 100 pending
- **M1**: Log warning if `streaming_start` returns non-200, notify user via TTS
- **M2**: Add short delay before disclosure on outbound calls
- **L1-L5**: Code cleanup pass

## Latency Analysis

Current pipeline latency breakdown (measured from Phase 1, tool calling adds overhead):

| Stage                       | Latency          | Notes                                            |
| --------------------------- | ---------------- | ------------------------------------------------ |
| Deepgram STT                | 220-270ms        | Streaming, interim results                       |
| vLLM Qwen 2.5 7B            | 110-130ms TTFB   | At low concurrency                               |
| ToolCallProcessor buffering | +50-200ms        | Buffers all tokens until LLMFullResponseEndFrame |
| Tool HTTP execution         | 5-2000ms         | Depends on endpoint (mock: ~30ms)                |
| Extra LLM turn (after tool) | +300-600ms       | Full LLM completion with tool result in context  |
| Kokoro TTS                  | 389-554ms        | With comma→period clause splitting               |
| **Total (no tools)**        | **~830ms**       |                                                  |
| **Total (with tool call)**  | **~1500-2500ms** | Filler speech masks perceived latency            |

### ToolCallProcessor Buffering Impact

The processor buffers ALL `LLMTextFrame` tokens and only replays them on `LLMFullResponseEndFrame`. This means:

- **Without tools**: adds ~50-200ms to the normal flow (time from first token to end-of-response)
- **With tools**: no additional latency (tool execution replaces the normal TTS path)

This is the correct tradeoff — without buffering, partial tool JSON would reach TTS and be spoken aloud.

### Tool Call Overhead

Each tool call adds one full LLM round-trip:

1. First LLM turn: generates tool call JSON (~130ms TTFT + ~200ms generation)
2. Filler speech plays while tool HTTP executes
3. Tool result injected into context
4. Second LLM turn: generates natural language response (~130ms TTFT + ~300ms generation)

**Net perceived latency**: ~500ms (filler speech starts immediately, masks the HTTP + second LLM turn). User hears "Un momento..." within 200ms of tool detection.

## Phase 1 Findings Status (from audit-2026-03-23.md)

| #   | Issue                           | Phase 2 Status                               |
| --- | ------------------------------- | -------------------------------------------- |
| C1  | TTS SENTENCE mode latency       | **RESOLVED** — comma→period trick            |
| C2  | MinIO sync blocks event loop    | **RESOLVED** — `upload_recording_async` used |
| C3  | Hardcoded IP in webhook         | **RESOLVED** — APP_PUBLIC_URL                |
| C4  | No engine.dispose on shutdown   | **RESOLVED** — in lifespan                   |
| C5  | Agent fallback silent           | **RESOLVED** — logged                        |
| C6  | No API authentication           | **STILL OPEN** — Phase 6 scope               |
| C7  | No webhook signature validation | **STILL OPEN** — C5 above                    |
| C9  | Unused `get_session()`          | **NOW USED** — by all API endpoints          |
| C10 | Small DB pool                   | **RESOLVED** — pool_size=20                  |
