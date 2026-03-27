# Pipesong — Advance vs. Scope

Last updated: 2026-03-27

## Overview

| Phase                         | Scope                                             | Status        | Advance |
| ----------------------------- | ------------------------------------------------- | ------------- | ------- |
| **0 — Benchmarks**            | Validate LLM, TTS, turn detection in Spanish      | `DONE`        | 100%    |
| **1 — First Call**            | Pipeline + Telnyx + basic API + recording         | `DONE`        | 100%    |
| **2 — Multi-Agent + Tools**   | Agent config, routing, function calling, webhooks | `DONE`        | 100%    |
| **3 — Knowledge Base**        | RAG pipeline, pgvector, retrieval                 | `DONE`        | 90%     |
| **4a — Latency Optimization** | Metrics wiring, sentence streaming, VAD tuning    | `IN PROGRESS` | 85%     |
| **4b — Conversation Flows**   | Flow engine, state machine, warm transfer         | `NOT STARTED` | 0%      |
| **5 — Analysis + Monitoring** | Post-call analysis, Prometheus, Grafana           | `NOT STARTED` | 0%      |
| **6 — Scale + Hardening**     | Overflow, batch calling, load testing             | `NOT STARTED` | 0%      |

---

## Phase 0 — Validate Assumptions (COMPLETE)

**Goal:** Kill the biggest risks before writing infrastructure code.
**Result:** All major decisions made. Custom voice generation deferred to later phase.

| #    | Activity                                                         | Status     | Notes                                                                                                |
| ---- | ---------------------------------------------------------------- | ---------- | ---------------------------------------------------------------------------------------------------- |
| 0.1  | Set up vLLM with Qwen 2.5 7B, Llama 3.1 8B, Gemma 2 9B           | `DONE`     | All 3 downloaded. vLLM 0.6.6 works. Gemma eliminated (AWQ incompatible).                             |
| 0.2  | LLM: 50 Spanish conversational prompts                           | `DONE`     | Qwen 50/50, Llama 50/50. Both natural Spanish. Qwen slightly better variety.                         |
| 0.3  | LLM: 20 function calling scenarios                               | `DONE`     | Qwen 60%, Llama 40% (prompt-based). Native tools need vLLM 0.7+.                                     |
| 0.4  | LLM: First-token latency at 1/10/20 concurrent                   | `DONE`     | Qwen: 22/94/130ms. Llama: 23/111/175ms. **5-10× better than planned.**                               |
| 0.5  | LLM: AWQ 4-bit vs full precision quality delta                   | `SKIPPED`  | Qwen AWQ quality is clearly sufficient.                                                              |
| 0.6  | LLM: RAG-grounded questions (20), measure hallucination          | `DONE`     | Both models: 0% hallucination, 5/5 unanswerable refused.                                             |
| 0.7  | TTS: Generate 20 Spanish sentences (Kokoro, Fish Speech, F5-TTS) | `DONE`     | Kokoro 3 voices + XTTS-v2 + Fish Speech S2-Pro. 100 phone-quality files.                             |
| 0.8  | TTS: Downsample to 8kHz G.711, evaluate quality                  | `DONE`     | User listened to all 100 samples. **Kokoro selected** with em_alex placeholder. Custom voices later. |
| 0.9  | TTS: Measure TTFB at 1/10 concurrent                             | `DONE`     | Kokoro: 115ms p50. XTTS: 2,393ms. Fish S2-Pro: 27,656ms.                                             |
| 0.10 | Turn detection: Record 20 Spanish conversation fragments         | `DEFERRED` | Evaluate with real phone audio in Phase 1.                                                           |
| 0.11 | Turn detection: Test LiveKit vs Pipecat Smart Turn               | `DEFERRED` | Blocked on 0.10. Models downloaded and ready.                                                        |
| 0.12 | Document results in `docs/phase0-benchmarks.md`                  | `DONE`     | Full results document written and updated.                                                           |

### Phase 0 Final Decisions

| Component          | Decision                                                      | Rationale                                                                                    |
| ------------------ | ------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| **LLM**            | Qwen 2.5 7B AWQ via vLLM 0.6.6                                | Best function calling (60%), fastest TTFT (130ms @20), 0% hallucination                      |
| **TTS**            | Kokoro, voice `em_alex` (placeholder)                         | Only real-time viable option (115ms). Custom voice generation planned for later.             |
| **STT**            | Deepgram Nova-3 (primary) + whisper-large-v3-turbo (fallback) | Deepgram: 150-300ms streaming. Fallback: 212ms, 100% Spanish detection.                      |
| **Turn detection** | Deferred to Phase 1                                           | Need real phone audio. Both LiveKit + Pipecat Smart Turn models ready.                       |
| **Custom voices**  | Deferred to post-Phase 1                                      | Kokoro em_alex is acceptable placeholder. Will generate custom Mexican Spanish voices later. |

---

## Phase 1 — First Phone Call (2-3 weeks)

**Goal:** Dial a number, hear disclosure, converse in Spanish, transcript stored.
**Exit:** 3-minute conversation works end-to-end.

| #                  | Activity                                                   | Status     | Notes                                                                                           |
| ------------------ | ---------------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------------------- |
| **Infrastructure** |                                                            |            |                                                                                                 |
| 1.1                | Docker Compose: PostgreSQL + MinIO                         | `DONE`     | Running on TensorDock via docker-compose                                                        |
| 1.2                | GPU server: vLLM (Qwen 2.5 7B AWQ) serving                 | `DONE`     | Port 8000, clean pipesong-venv, TTFB 110ms                                                      |
| 1.3                | GPU server: Kokoro TTS (em_alex) serving                   | `DONE`     | Native in Pipecat. **TTFB 389-554ms** (down from 800-2353ms via comma→period clause splitting). |
| 1.4                | GPU server: faster-whisper (large-v3-turbo) fallback       | `DEFERRED` | Error logging added. Hot-swap deferred to Phase 6.                                              |
| 1.5                | Telnyx account: SIP trunk + first phone number             | `DONE`     | +12678840093 (US), TeXML app "Pipesong", webhook pointing to TensorDock                         |
| **Pipeline**       |                                                            |            |                                                                                                 |
| 1.6                | Pipecat app with Telnyx WebSocket serializer               | `DONE`     | FastAPI + parse_telephony_websocket() + TelnyxFrameSerializer                                   |
| 1.7                | Deepgram STT plugin (streaming)                            | `DONE`     | Nova-3, Spanish, 220-270ms TTFB, interim results working                                        |
| 1.8                | STT fallback: switch to faster-whisper on Deepgram failure | `DEFERRED` | Error handler logs failures. Hot-swap deferred to Phase 6.                                      |
| 1.9                | LLM plugin → local vLLM (OpenAI-compatible)                | `DONE`     | Qwen 2.5 7B AWQ, 110ms TTFB, frequency_penalty=1.2                                              |
| 1.10               | TTS plugin (Kokoro, streaming)                             | `DONE`     | em_alex voice, language=es. SpanishOnlyFilter strips CJK from Qwen output.                      |
| 1.11               | Silero VAD + turn detector                                 | `DONE`     | Pipecat Smart Turn v3 auto-loaded. Working on real calls.                                       |
| 1.12               | Recording disclosure: pre-recorded audio at call start     | `DONE`     | TTSSpeakFrame queued before pipeline run. append_to_context=False.                              |
| **API + Storage**  |                                                            |            |                                                                                                 |
| 1.13               | PostgreSQL schema: agents, calls, transcripts              | `DONE`     | Timezone-aware columns. Agent + Call + Transcript models.                                       |
| 1.14               | FastAPI: `POST /agents`, `GET /agents`, `GET /calls`       | `DONE`     | Working. Agent created via API.                                                                 |
| 1.15               | Call recording pipeline: audio → MinIO                     | `DONE`     | AudioBufferProcessor → WAV → MinIO upload in finally block.                                     |
| 1.16               | Transcript storage: Deepgram transcript → PostgreSQL       | `DONE`     | TranscriptCapture processor saves user + assistant turns.                                       |

---

## Phase 2 — Multi-Agent + Tools (2-3 weeks)

**Goal:** 3 agents on 3 numbers, each with tools. Outbound calls work.
**Exit:** Agent A books via API, Agent B checks status, Agent C answers questions.

| #                    | Activity                                                          | Status     | Notes                                                                                           |
| -------------------- | ----------------------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------------------- |
| **Agent Config**     |                                                                   |            |                                                                                                 |
| 2.1                  | Full agent model in PostgreSQL (prompt, voice, LLM, tools, vars)  | `DONE`     | +6 columns: tools (JSONB), webhook_url, webhook_secret, variables, max_call_duration, is_active |
| 2.2                  | Phone number → agent routing (Telnyx webhook → DB lookup)         | `DONE`     | Phase 1 routing + is_active filter. Outbound via query params.                                  |
| 2.3                  | Dynamic variables: `{{var}}` substitution in prompts              | `DONE`     | Agent variables + per-call context (from_number, to_number, call_id)                            |
| **Function Calling** |                                                                   |            |                                                                                                 |
| 2.4                  | Tool definition per agent (schema in DB)                          | `DONE`     | JSONB tools array. format_tools_prompt() for Spanish system prompt injection.                   |
| 2.5                  | Sync execution: wait for result, speak about it                   | `DONE`     | ToolCallProcessor buffers LLM output, detects JSON, executes HTTP, injects result.              |
| 2.6                  | Async execution: speak filler while tool runs                     | `DONE`     | Filler phrases play via TTSSpeakFrame before tool HTTP call.                                    |
| 2.7                  | HMAC signature on outgoing webhook requests                       | `DONE`     | HMAC-SHA256 in X-Pipesong-Signature header.                                                     |
| 2.8                  | Built-in tools: end_call, transfer_call                           | `DONE`     | end_call: TTSSpeakFrame + EndFrame. transfer_call: Telnyx REST API.                             |
| **Telephony**        |                                                                   |            |                                                                                                 |
| 2.9                  | Outbound call API: `POST /calls/outbound`                         | `DONE`     | Telnyx Call Control API + streaming_start. Verified end-to-end.                                 |
| 2.10                 | Cold call transfer via Telnyx REST API                            | `DONE`     | Via transfer_call built-in tool.                                                                |
| 2.11                 | DTMF detection via WebSocket events                               | `DEFERRED` | Deferred to Phase 4b (conversation flows).                                                      |
| **Webhooks**         |                                                                   |            |                                                                                                 |
| 2.12                 | `call_started`, `call_ended` events to webhook_url                | `DONE`     | Fire-and-forget via asyncio.create_task.                                                        |
| 2.13                 | Webhook payload: call_id, agent_id, numbers, duration, transcript | `DONE`     | call_ended includes full transcript array.                                                      |

---

## Phase 3 — Knowledge Base (2 weeks)

**Goal:** Upload docs, agent answers from them accurately.
**Exit:** 20-page manual uploaded, agent answers 8/10 questions correctly.

| #    | Activity                                                             | Status     | Notes                                                                  |
| ---- | -------------------------------------------------------------------- | ---------- | ---------------------------------------------------------------------- |
| 3.1  | Upload API: PDF, DOCX, TXT, MD, CSV, HTML                            | `DONE`     | pymupdf4llm + python-docx + markdownify + stdlib                       |
| 3.2  | Text extraction + chunking (512 tokens, 50 overlap)                  | `DONE`     | tiktoken cl100k_base tokenizer                                         |
| 3.3  | Embedding: local multilingual-e5-small → pgvector                    | `DONE`     | 384 dims, GPU inference ~10ms, loaded at startup                       |
| 3.4  | Per-agent KB assignment (foreign key)                                | `DONE`     | knowledge_base_id FK on Agent, kb_chunk_count, kb_similarity_threshold |
| 3.5  | Retrieval: embed utterance → cosine similarity → top-K → LLM context | `DONE`     | RAGProcessor, 11-32ms total, replaces previous context each turn       |
| 3.6  | HNSW index on pgvector for fast retrieval                            | `DONE`     | m=16, ef_construction=64                                               |
| 3.7  | Configurable: chunk count, similarity threshold per agent            | `DONE`     | kb_chunk_count (default 2), kb_similarity_threshold (default 0.5)      |
| 3.8  | URL sources: fetch and index web pages                               | `DEFERRED` | Phase 4b+                                                              |
| 3.9  | Auto-refresh: re-crawl URLs every 24h                                | `DEFERRED` | Phase 4b+                                                              |
| 3.10 | KB status API: indexing progress, counts                             | `DONE`     | GET /knowledge-bases/{id} returns status + counts                      |

---

## Phase 4a — Latency Optimization (2-3 weeks)

**Goal:** p50 <1,000ms with per-turn instrumentation proving it.
**Exit:** p50 <1,000ms over 50 test calls, proven by `call_latency` table. Sentence streaming measurably reduces e2e vs baseline.

| #                                        | Activity                                                     | Status        | Notes                                                          |
| ---------------------------------------- | ------------------------------------------------------------ | ------------- | -------------------------------------------------------------- |
| **Latency Instrumentation (week 1)**     |                                                              |               |                                                                |
| 4a.1                                     | MetricsCollector processor: intercept MetricsFrame + VAD     | `DONE`        | Intercepts TTFBMetricsData, classifies by service name         |
| 4a.2                                     | Add UserBotLatencyObserver to PipelineTask                   | `DEFERRED`    | MetricsCollector handles it; observer adds if e2e needs fix    |
| 4a.3                                     | Persist to PostgreSQL (call_latency table)                   | `DONE`        | CallLatency model, auto-flush on LLMFullResponseEndFrame       |
| 4a.4                                     | API: `GET /calls/{id}/latency`                               | `DONE`        | Per-turn breakdown + summary averages                          |
| 4a.5                                     | Aggregation: `GET /agents/{id}/latency` p50/p90/p95/p99      | `DONE`        | ?hours=N query param (1-720h, default 24)                      |
| 4a.6                                     | Baseline run: 20 calls + comma vs period A/B test            | `NOT STARTED` | Establish numbers + inform SentenceStreamBuffer design         |
| **Sentence Streaming (week 2)**          |                                                              |               |                                                                |
| 4a.7                                     | SentenceStreamBuffer processor (Spanish-aware boundaries)    | `DONE`        | .?! boundaries, abbreviation exclusions, interruption discard  |
| 4a.8                                     | ToolCallProcessor streaming mode (early bail-out heuristic)  | `DONE`        | First token: {/[/tool name → buffer, else → stream passthrough |
| 4a.9                                     | TTS request queuing: emit sentences as TTSSpeakFrames        | `DONE`        | SentenceStreamBuffer emits TTSSpeakFrames per sentence         |
| 4a.10                                    | Interruption: cancel pending TTSSpeakFrames + discard buffer | `DONE`        | StartInterruptionFrame clears buffer in SentenceStreamBuffer   |
| 4a.11                                    | Comma→period hack decision based on A/B results              | `NOT STARTED` | Needs GPU baseline testing (4a.6)                              |
| 4a.12                                    | Measure improvement vs baseline                              | `NOT STARTED` | Needs GPU baseline testing (4a.6)                              |
| **VAD + Interruption Tuning (week 2-3)** |                                                              |               |                                                                |
| 4a.13                                    | Agent-level vad_stop_secs + vad_confidence columns           | `DONE`        | Nullable columns on Agent, passed to SileroVADAnalyzer         |
| 4a.14                                    | Add STTMuteFilter: FIRST_SPEECH + FUNCTION_CALL strategies   | `DONE`        | Suppresses interruption during disclosure + tool execution     |

---

## Phase 4b — Conversation Flows (3-4 weeks)

**Goal:** YAML-defined multi-step conversations with state management and variable extraction.
**Prerequisite:** Phase 4a (instrumentation needed to catch latency regressions from extra LLM calls).
**Exit:** 5-state appointment booking flow completes e2e. LLM transition evaluation <500ms. Warm transfer bridges two call legs.

| #                                  | Activity                                                        | Status        | Notes                                           |
| ---------------------------------- | --------------------------------------------------------------- | ------------- | ----------------------------------------------- |
| **Flow Schema Design (week 1)**    |                                                                 |               |                                                 |
| 4b.1                               | YAML flow schema definition                                     | `NOT STARTED` | initial_state, states, transitions, end         |
| 4b.2                               | Variable conditions (fast, no LLM)                              | `NOT STARTED` | Evaluated first — free                          |
| 4b.3                               | LLM conditions (slow, costs a call)                             | `NOT STARTED` | Only when variable conditions can't match       |
| 4b.4                               | Flow validation at agent creation                               | `NOT STARTED` | Orphan states, missing targets, no-exit states  |
| 4b.5                               | Store validated flow as JSON in agent record                    | `NOT STARTED` | Parse YAML on input                             |
| **Flow Engine Runtime (week 2-3)** |                                                                 |               |                                                 |
| 4b.6                               | FlowEngine class: state tracking, variables, transition history | `NOT STARTED` | Initialized per call                            |
| 4b.7                               | Per-state prompt injection (append on entry, remove on exit)    | `NOT STARTED` | Prevents prompt accumulation                    |
| 4b.8                               | Variable extraction via LLM after each assistant turn           | `NOT STARTED` | Only current state's schema                     |
| 4b.9                               | Transition evaluation: variable → LLM (ordered)                 | `NOT STARTED` | First match fires                               |
| 4b.10                              | State-scoped tools: hide unavailable tools per state            | `NOT STARTED` |                                                 |
| 4b.11                              | End states: farewell TTS + EndFrame                             | `NOT STARTED` |                                                 |
| 4b.12                              | Persist flow state to PostgreSQL (call_flow_state table)        | `NOT STARTED` | Debug stuck flows, post-call analysis           |
| **Flow API (week 3)**              |                                                                 |               |                                                 |
| 4b.13                              | PATCH /agents/{id} accepts flow field (YAML/JSON)               | `NOT STARTED` | Validates before saving                         |
| 4b.14                              | GET /calls/{id}/flow — execution trace                          | `NOT STARTED` | States, transitions, variables, timestamps      |
| 4b.15                              | GET /flow-templates — built-in examples                         | `NOT STARTED` | Booking, support ticket, survey                 |
| **Warm Call Transfer (week 3-4)**  |                                                                 |               |                                                 |
| 4b.16                              | Two-leg transfer via Telnyx Call Control API                    | `NOT STARTED` | Context handoff then bridge                     |
| 4b.17                              | Transfer as flow action (target_number + context_prompt)        | `NOT STARTED` | Flow engine orchestrates                        |
| 4b.18                              | Fallback: 30s timeout → return to previous state                | `NOT STARTED` |                                                 |
| **Deferred from 4a**               |                                                                 |               |                                                 |
| 4b.19                              | Silence reminders after configurable timeout                    | `NOT STARTED` | "¿Sigue ahí?" + graceful end after 2× timeout   |
| 4b.20                              | Pre-cached responses (if TTS bottleneck confirmed by 4a data)   | `NOT STARTED` | Code-controlled phrases only, skip LLM matching |

---

## Phase 5 — Call Analysis + Monitoring (2 weeks)

**Goal:** Post-call insights. Grafana dashboards. Alerting.
**Exit:** Grafana live, post-call analysis classifies 90%+ correctly.

| #    | Activity                                                   | Status        | Notes                                    |
| ---- | ---------------------------------------------------------- | ------------- | ---------------------------------------- |
| 5.1  | Post-call analysis: send transcript to LLM on call_ended   | `NOT STARTED` |                                          |
| 5.2  | Extract: summary, sentiment, success/failure               | `NOT STARTED` |                                          |
| 5.3  | Custom extractors per agent (boolean/text/number)          | `NOT STARTED` |                                          |
| 5.4  | Store analysis in PostgreSQL, fire `call_analyzed` webhook | `NOT STARTED` |                                          |
| 5.5  | Prometheus metrics exporter                                | `NOT STARTED` | calls, latency, errors, fallbacks        |
| 5.6  | Grafana dashboards                                         | `NOT STARTED` | Volume, latency, success, per-agent      |
| 5.7  | Alerting: latency p95, error rate, fallback duration       | `NOT STARTED` |                                          |
| 5.8  | `GET /calls` with filters                                  | `NOT STARTED` | Agent, date, success, sentiment          |
| 5.9  | `GET /calls/{id}` full detail                              | `NOT STARTED` | Transcript, analysis, latency, recording |
| 5.10 | `GET /agents/{id}/stats` aggregated metrics                | `NOT STARTED` |                                          |

---

## Phase 6 — Scale + Production Hardening (3-4 weeks)

**Goal:** 30-50 concurrent calls, auto-overflow, batch calling.
**Exit:** 30 concurrent calls for 30 min at p95 <1,500ms. Batch of 100 calls completes.

| #                      | Activity                                                | Status        | Notes                           |
| ---------------------- | ------------------------------------------------------- | ------------- | ------------------------------- |
| **LLM Overflow**       |                                                         |               |                                 |
| 6.1                    | Monitor vLLM queue depth                                | `NOT STARTED` |                                 |
| 6.2                    | Auto-route to Groq when threshold exceeded              | `NOT STARTED` |                                 |
| 6.3                    | Dashboard panel: overflow rate                          | `NOT STARTED` |                                 |
| **Batch Calling**      |                                                         |               |                                 |
| 6.4                    | `POST /batch-calls` with CSV                            | `NOT STARTED` | Phone numbers + variables       |
| 6.5                    | Concurrency control + rate limiting                     | `NOT STARTED` | Telnyx CPS limits               |
| 6.6                    | Per-row status tracking                                 | `NOT STARTED` | pending → dialing → done/failed |
| 6.7                    | Voicemail detection on outbound                         | `NOT STARTED` |                                 |
| **Reliability**        |                                                         |               |                                 |
| 6.8                    | Health checks: vLLM, Kokoro, Deepgram, PostgreSQL       | `NOT STARTED` |                                 |
| 6.9                    | Auto-restart on crash (Docker + systemd)                | `NOT STARTED` |                                 |
| 6.10                   | Graceful shutdown: finish active calls, then exit       | `NOT STARTED` |                                 |
| 6.11                   | Connection retry with backoff (Deepgram, vLLM)          | `NOT STARTED` |                                 |
| **LiveKit Evaluation** |                                                         |               |                                 |
| 6.12                   | Benchmark Pipecat+Telnyx vs LiveKit at 30-50 concurrent | `NOT STARTED` | If consistently >20             |
| 6.13                   | Document decision in `docs/livekit-evaluation.md`       | `NOT STARTED` |                                 |
| **Load Testing**       |                                                         |               |                                 |
| 6.14                   | Simulate 10/20/30/40/50 concurrent calls                | `NOT STARTED` |                                 |
| 6.15                   | Measure latency degradation curve                       | `NOT STARTED` |                                 |
| 6.16                   | Document scaling thresholds                             | `NOT STARTED` | "Add second GPU at X"           |

---

## Success Milestones

| Milestone        | Definition                                                  | Target Phase | Status                                                              |
| ---------------- | ----------------------------------------------------------- | ------------ | ------------------------------------------------------------------- |
| Models validated | LLM, TTS, turn detector pass Spanish benchmarks             | 0            | `DONE`                                                              |
| First call       | AI answers phone, converses in Spanish, stores transcript   | 1            | `DONE` — conversation + disclosure + transcript + recording         |
| Multi-agent      | 5+ agents with KB handling calls                            | 3            | `NOT STARTED`                                                       |
| Optimized        | p50 <1,000ms over 100 test calls, proven by instrumentation | 4a           | `EARLY` — ~830ms achieved in Phase 1, formal validation in Phase 4a |
| Flows working    | 5-state appointment flow completes e2e, warm transfer works | 4b           | `NOT STARTED`                                                       |
| Observable       | Grafana live, post-call analysis working                    | 5            | `NOT STARTED`                                                       |
| Production       | 30 concurrent calls, overflow, batch complete               | 6            | `NOT STARTED`                                                       |
| Cost target      | Operating at <$0.03/min all-in                              | 6            | `NOT STARTED`                                                       |

---

## Blockers & Decisions Log

| Date       | Item                                   | Status      | Resolution                                                                                                                                                         |
| ---------- | -------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 2026-03-22 | Need GPU server for Phase 0 benchmarks | `RESOLVED`  | TensorDock RTX 4090 KVM. IP: 206.168.83.248. ~$3-4 spent.                                                                                                          |
| 2026-03-22 | vLLM V1 engine crashes on TensorDock   | `RESOLVED`  | Downgraded to vLLM 0.6.6 (V0).                                                                                                                                     |
| 2026-03-22 | LLM model selection                    | `RESOLVED`  | **Qwen 2.5 7B AWQ.**                                                                                                                                               |
| 2026-03-22 | STT fallback model                     | `RESOLVED`  | **whisper-large-v3-turbo** (NOT distil-large-v3 — English only).                                                                                                   |
| 2026-03-23 | TTS engine for Spanish                 | `RESOLVED`  | **Kokoro, em_alex placeholder.** Custom voice generation deferred. XTTS/Fish too slow for real-time.                                                               |
| 2026-03-23 | HTTP audio player                      | `RESOLVED`  | Port 8765 closed, server killed. Review complete.                                                                                                                  |
| 2026-03-22 | LLM latency much better than planned   | `INFO`      | TTFT 130ms @20 concurrent vs planned 500-800ms. Groq overflow threshold ~40-60.                                                                                    |
| 2026-03-22 | Fish S2-Pro uses 22GB VRAM             | `INFO`      | Cannot coexist with LLM. Only viable offline.                                                                                                                      |
| 2026-03-23 | Telnyx Mexico numbers                  | `RESOLVED`  | Mexico only has toll-free at $20/month. Bought US local +12678840093 at $1/month instead.                                                                          |
| 2026-03-23 | Turn detector for Spanish              | `RESOLVED`  | Pipecat Smart Turn v3 auto-loaded and working on real calls.                                                                                                       |
| 2026-03-23 | Custom voice generation                | `OPEN`      | Kokoro em_alex is placeholder. Need to generate custom Mexican Spanish voices (post-Phase 1).                                                                      |
| 2026-03-23 | First phone conversation achieved      | `INFO`      | Full conversation: greeting → problem diagnosis → router reset → resolution → goodbye. ~10 turns.                                                                  |
| 2026-03-23 | Qwen Chinese code-switching            | `MITIGATED` | Qwen 2.5 switches to Chinese mid-response. SpanishOnlyFilter strips CJK before TTS. System prompt says "NUNCA uses otro idioma." Underlying issue: model weakness. |
| 2026-03-23 | Kokoro TTS latency in pipeline         | `RESOLVED`  | Root cause: Pipecat SENTENCE mode buffers until period. Fix: comma→period trick in SpanishOnlyFilter. TTFB 800-2353ms → **389-554ms**.                             |
| 2026-03-23 | Garbled Spanish pronunciation          | `RESOLVED`  | Space-fixing regex in SpanishOnlyFilter: inserts spaces before ¿¡, after .!?,;: and at camelCase boundaries. Combined with SENTENCE mode, pronunciation is good.   |
| 2026-03-24 | Audit fixes C1-C5, C9-C10 applied      | `RESOLVED`  | TTS mode configurable via env, hardcoded IP removed, engine.dispose on shutdown, agent fallback logged, DB pool tuned, async MinIO wrapper.                        |
| 2026-03-24 | TextAggregationMode.WORD doesn't exist | `RESOLVED`  | Only SENTENCE and TOKEN in Pipecat 0.0.106. Removed WORD from mode_map.                                                                                            |
| 2026-03-24 | TOKEN mode: fast but unrecognizable    | `INFO`      | 123ms TTFB but Kokoro gets sub-word fragments ("Con", "esa", "pod") — too small for Spanish phonemization. SENTENCE + comma→period is the sweet spot.              |
| 2026-03-24 | Prosody refinement needed              | `OPEN`      | Kokoro needs slightly more pause after periods and commas. Tune voice speed or add silence frames. Minor polish.                                                   |
| 2026-03-23 | Pipecat v0.0.106 API changes           | `INFO`      | Many breaking changes from older docs. All resolved.                                                                                                               |
| 2026-03-24 | TensorDock GPU running                 | `INFO`      | Instance running at $0.35/hr. Stop when done.                                                                                                                      |
