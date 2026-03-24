# Pipesong — Advance vs. Scope

Last updated: 2026-03-24

## Overview

| Phase | Scope | Status | Advance |
|---|---|---|---|
| **0 — Benchmarks** | Validate LLM, TTS, turn detection in Spanish | `DONE` | 100% |
| **1 — First Call** | Pipeline + Telnyx + basic API + recording | `IN PROGRESS` | 75% |
| **2 — Multi-Agent + Tools** | Agent config, routing, function calling, webhooks | `NOT STARTED` | 0% |
| **3 — Knowledge Base** | RAG pipeline, pgvector, retrieval | `NOT STARTED` | 0% |
| **4 — Latency + Flows** | Sentence streaming, caching, flow engine | `NOT STARTED` | 0% |
| **5 — Analysis + Monitoring** | Post-call analysis, Prometheus, Grafana | `NOT STARTED` | 0% |
| **6 — Scale + Hardening** | Overflow, batch calling, load testing | `NOT STARTED` | 0% |

---

## Phase 0 — Validate Assumptions (COMPLETE)

**Goal:** Kill the biggest risks before writing infrastructure code.
**Result:** All major decisions made. Custom voice generation deferred to later phase.

| # | Activity | Status | Notes |
|---|---|---|---|
| 0.1 | Set up vLLM with Qwen 2.5 7B, Llama 3.1 8B, Gemma 2 9B | `DONE` | All 3 downloaded. vLLM 0.6.6 works. Gemma eliminated (AWQ incompatible). |
| 0.2 | LLM: 50 Spanish conversational prompts | `DONE` | Qwen 50/50, Llama 50/50. Both natural Spanish. Qwen slightly better variety. |
| 0.3 | LLM: 20 function calling scenarios | `DONE` | Qwen 60%, Llama 40% (prompt-based). Native tools need vLLM 0.7+. |
| 0.4 | LLM: First-token latency at 1/10/20 concurrent | `DONE` | Qwen: 22/94/130ms. Llama: 23/111/175ms. **5-10× better than planned.** |
| 0.5 | LLM: AWQ 4-bit vs full precision quality delta | `SKIPPED` | Qwen AWQ quality is clearly sufficient. |
| 0.6 | LLM: RAG-grounded questions (20), measure hallucination | `DONE` | Both models: 0% hallucination, 5/5 unanswerable refused. |
| 0.7 | TTS: Generate 20 Spanish sentences (Kokoro, Fish Speech, F5-TTS) | `DONE` | Kokoro 3 voices + XTTS-v2 + Fish Speech S2-Pro. 100 phone-quality files. |
| 0.8 | TTS: Downsample to 8kHz G.711, evaluate quality | `DONE` | User listened to all 100 samples. **Kokoro selected** with em_alex placeholder. Custom voices later. |
| 0.9 | TTS: Measure TTFB at 1/10 concurrent | `DONE` | Kokoro: 115ms p50. XTTS: 2,393ms. Fish S2-Pro: 27,656ms. |
| 0.10 | Turn detection: Record 20 Spanish conversation fragments | `DEFERRED` | Evaluate with real phone audio in Phase 1. |
| 0.11 | Turn detection: Test LiveKit vs Pipecat Smart Turn | `DEFERRED` | Blocked on 0.10. Models downloaded and ready. |
| 0.12 | Document results in `docs/phase0-benchmarks.md` | `DONE` | Full results document written and updated. |

### Phase 0 Final Decisions

| Component | Decision | Rationale |
|---|---|---|
| **LLM** | Qwen 2.5 7B AWQ via vLLM 0.6.6 | Best function calling (60%), fastest TTFT (130ms @20), 0% hallucination |
| **TTS** | Kokoro, voice `em_alex` (placeholder) | Only real-time viable option (115ms). Custom voice generation planned for later. |
| **STT** | Deepgram Nova-3 (primary) + whisper-large-v3-turbo (fallback) | Deepgram: 150-300ms streaming. Fallback: 212ms, 100% Spanish detection. |
| **Turn detection** | Deferred to Phase 1 | Need real phone audio. Both LiveKit + Pipecat Smart Turn models ready. |
| **Custom voices** | Deferred to post-Phase 1 | Kokoro em_alex is acceptable placeholder. Will generate custom Mexican Spanish voices later. |

---

## Phase 1 — First Phone Call (2-3 weeks)

**Goal:** Dial a number, hear disclosure, converse in Spanish, transcript stored.
**Exit:** 3-minute conversation works end-to-end.

| # | Activity | Status | Notes |
|---|---|---|---|
| **Infrastructure** | | | |
| 1.1 | Docker Compose: PostgreSQL + MinIO | `DONE` | Running on TensorDock via docker-compose |
| 1.2 | GPU server: vLLM (Qwen 2.5 7B AWQ) serving | `DONE` | Port 8000, clean pipesong-venv, TTFB 110ms |
| 1.3 | GPU server: Kokoro TTS (em_alex) serving | `DONE` | Native in Pipecat. **TTFB 389-554ms** (down from 800-2353ms via comma→period clause splitting). |
| 1.4 | GPU server: faster-whisper (large-v3-turbo) fallback | `NOT STARTED` | Not loaded yet |
| 1.5 | Telnyx account: SIP trunk + first phone number | `DONE` | +12678840093 (US), TeXML app "Pipesong", webhook pointing to TensorDock |
| **Pipeline** | | | |
| 1.6 | Pipecat app with Telnyx WebSocket serializer | `DONE` | FastAPI + parse_telephony_websocket() + TelnyxFrameSerializer |
| 1.7 | Deepgram STT plugin (streaming) | `DONE` | Nova-3, Spanish, 220-270ms TTFB, interim results working |
| 1.8 | STT fallback: switch to faster-whisper on Deepgram failure | `NOT STARTED` | |
| 1.9 | LLM plugin → local vLLM (OpenAI-compatible) | `DONE` | Qwen 2.5 7B AWQ, 110ms TTFB, frequency_penalty=1.2 |
| 1.10 | TTS plugin (Kokoro, streaming) | `DONE` | em_alex voice, language=es. SpanishOnlyFilter strips CJK from Qwen output. |
| 1.11 | Silero VAD + turn detector | `DONE` | Pipecat Smart Turn v3 auto-loaded. Working on real calls. |
| 1.12 | Recording disclosure: pre-recorded audio at call start | `NOT STARTED` | Legal requirement |
| **API + Storage** | | | |
| 1.13 | PostgreSQL schema: agents, calls, transcripts | `DONE` | Timezone-aware columns. Agent + Call + Transcript models. |
| 1.14 | FastAPI: `POST /agents`, `GET /agents`, `GET /calls` | `DONE` | Working. Agent created via API. |
| 1.15 | Call recording pipeline: audio → MinIO | `NOT STARTED` | |
| 1.16 | Transcript storage: Deepgram transcript → PostgreSQL | `NOT STARTED` | Call records created but transcripts not persisted yet. |

---

## Phase 2 — Multi-Agent + Tools (2-3 weeks)

**Goal:** 3 agents on 3 numbers, each with tools. Outbound calls work.
**Exit:** Agent A books via API, Agent B checks status, Agent C answers questions.

| # | Activity | Status | Notes |
|---|---|---|---|
| **Agent Config** | | | |
| 2.1 | Full agent model in PostgreSQL (prompt, voice, LLM, tools, vars) | `NOT STARTED` | |
| 2.2 | Phone number → agent routing (Telnyx webhook → DB lookup) | `NOT STARTED` | |
| 2.3 | Dynamic variables: `{{var}}` substitution in prompts | `NOT STARTED` | |
| **Function Calling** | | | |
| 2.4 | Tool definition per agent (schema in DB) | `NOT STARTED` | |
| 2.5 | Sync execution: wait for result, speak about it | `NOT STARTED` | |
| 2.6 | Async execution: speak filler while tool runs | `NOT STARTED` | |
| 2.7 | HMAC signature on outgoing webhook requests | `NOT STARTED` | |
| 2.8 | Built-in tools: end_call, transfer_call | `NOT STARTED` | |
| **Telephony** | | | |
| 2.9 | Outbound call API: `POST /calls` | `NOT STARTED` | |
| 2.10 | Cold call transfer via Telnyx REST API | `NOT STARTED` | |
| 2.11 | DTMF detection via WebSocket events | `NOT STARTED` | |
| **Webhooks** | | | |
| 2.12 | `call_started`, `call_ended` events to webhook_url | `NOT STARTED` | |
| 2.13 | Webhook payload: call_id, agent_id, numbers, duration, transcript | `NOT STARTED` | |

---

## Phase 3 — Knowledge Base (2 weeks)

**Goal:** Upload docs, agent answers from them accurately.
**Exit:** 20-page manual uploaded, agent answers 8/10 questions correctly.

| # | Activity | Status | Notes |
|---|---|---|---|
| 3.1 | Upload API: PDF, DOCX, TXT, MD, CSV, HTML | `NOT STARTED` | |
| 3.2 | Text extraction + chunking (512 tokens, 50 overlap) | `NOT STARTED` | |
| 3.3 | Embedding: local `all-MiniLM-L6-v2` → pgvector | `NOT STARTED` | |
| 3.4 | Per-agent KB assignment (foreign key) | `NOT STARTED` | |
| 3.5 | Retrieval: embed utterance → cosine similarity → top-3 → LLM context | `NOT STARTED` | Target <50ms |
| 3.6 | HNSW index on pgvector for fast retrieval | `NOT STARTED` | |
| 3.7 | Configurable: chunk count, similarity threshold per agent | `NOT STARTED` | |
| 3.8 | URL sources: fetch and index web pages | `NOT STARTED` | |
| 3.9 | Auto-refresh: re-crawl URLs every 24h | `NOT STARTED` | Background worker |
| 3.10 | KB status API: indexing progress, counts | `NOT STARTED` | |

---

## Phase 4 — Latency Optimization + Conversation Flows (4-6 weeks)

**Goal:** p50 <1,000ms. YAML-defined conversation flows work.
**Exit:** 100 test calls at p50 <1,000ms. 5-state booking flow completes.

| # | Activity | Status | Notes |
|---|---|---|---|
| **Sentence Streaming (week 1-2)** | | | |
| 4.1 | Sentence boundary detection in LLM output stream | `NOT STARTED` | `.` `?` `!` `\n` |
| 4.2 | Send each sentence to TTS immediately | `NOT STARTED` | While LLM generates next |
| 4.3 | Stream audio chunks to caller as produced | `NOT STARTED` | |
| 4.4 | Handle interruption during streaming | `NOT STARTED` | Cancel remaining TTS |
| **Pre-cached Responses (week 2)** | | | |
| 4.5 | Generate TTS for `precached_phrases` at agent creation | `NOT STARTED` | |
| 4.6 | Pattern matching: play cached audio on match | `NOT STARTED` | 0ms TTS |
| 4.7 | Cache invalidation on voice settings change | `NOT STARTED` | |
| **Turn-taking (week 2-3)** | | | |
| 4.8 | Per-agent interruption sensitivity | `NOT STARTED` | Configurable threshold |
| 4.9 | Block interruptions during critical speech | `NOT STARTED` | Tool results, disclosure |
| 4.10 | Silence reminders after configurable timeout | `NOT STARTED` | Stretch goal |
| **Conversation Flows (week 3-5)** | | | |
| 4.11 | YAML flow parser + validator | `NOT STARTED` | Detect orphan states, missing transitions |
| 4.12 | Flow engine: state machine runtime | `NOT STARTED` | Current state, variables, transitions |
| 4.13 | Equation conditions: `variable == value`, AND, OR, CONTAINS | `NOT STARTED` | Evaluated first |
| 4.14 | Prompt conditions: LLM-evaluated natural language | `NOT STARTED` | Evaluated after equations |
| 4.15 | Per-state prompt injection alongside global system prompt | `NOT STARTED` | |
| 4.16 | Variable extraction: LLM → named flow variables | `NOT STARTED` | |
| 4.17 | Warm call transfer: second SIP leg, context, bridge | `NOT STARTED` | |
| **Latency Instrumentation (week 5-6)** | | | |
| 4.18 | Timestamp every pipeline stage per turn | `NOT STARTED` | VAD, STT, LLM, TTS |
| 4.19 | Store latency per call in PostgreSQL | `NOT STARTED` | |
| 4.20 | API: `GET /calls/{id}/latency` with per-turn breakdown | `NOT STARTED` | |
| 4.21 | Aggregate p50/p90/p95/p99 per agent | `NOT STARTED` | |

---

## Phase 5 — Call Analysis + Monitoring (2 weeks)

**Goal:** Post-call insights. Grafana dashboards. Alerting.
**Exit:** Grafana live, post-call analysis classifies 90%+ correctly.

| # | Activity | Status | Notes |
|---|---|---|---|
| 5.1 | Post-call analysis: send transcript to LLM on call_ended | `NOT STARTED` | |
| 5.2 | Extract: summary, sentiment, success/failure | `NOT STARTED` | |
| 5.3 | Custom extractors per agent (boolean/text/number) | `NOT STARTED` | |
| 5.4 | Store analysis in PostgreSQL, fire `call_analyzed` webhook | `NOT STARTED` | |
| 5.5 | Prometheus metrics exporter | `NOT STARTED` | calls, latency, errors, fallbacks |
| 5.6 | Grafana dashboards | `NOT STARTED` | Volume, latency, success, per-agent |
| 5.7 | Alerting: latency p95, error rate, fallback duration | `NOT STARTED` | |
| 5.8 | `GET /calls` with filters | `NOT STARTED` | Agent, date, success, sentiment |
| 5.9 | `GET /calls/{id}` full detail | `NOT STARTED` | Transcript, analysis, latency, recording |
| 5.10 | `GET /agents/{id}/stats` aggregated metrics | `NOT STARTED` | |

---

## Phase 6 — Scale + Production Hardening (3-4 weeks)

**Goal:** 30-50 concurrent calls, auto-overflow, batch calling.
**Exit:** 30 concurrent calls for 30 min at p95 <1,500ms. Batch of 100 calls completes.

| # | Activity | Status | Notes |
|---|---|---|---|
| **LLM Overflow** | | | |
| 6.1 | Monitor vLLM queue depth | `NOT STARTED` | |
| 6.2 | Auto-route to Groq when threshold exceeded | `NOT STARTED` | |
| 6.3 | Dashboard panel: overflow rate | `NOT STARTED` | |
| **Batch Calling** | | | |
| 6.4 | `POST /batch-calls` with CSV | `NOT STARTED` | Phone numbers + variables |
| 6.5 | Concurrency control + rate limiting | `NOT STARTED` | Telnyx CPS limits |
| 6.6 | Per-row status tracking | `NOT STARTED` | pending → dialing → done/failed |
| 6.7 | Voicemail detection on outbound | `NOT STARTED` | |
| **Reliability** | | | |
| 6.8 | Health checks: vLLM, Kokoro, Deepgram, PostgreSQL | `NOT STARTED` | |
| 6.9 | Auto-restart on crash (Docker + systemd) | `NOT STARTED` | |
| 6.10 | Graceful shutdown: finish active calls, then exit | `NOT STARTED` | |
| 6.11 | Connection retry with backoff (Deepgram, vLLM) | `NOT STARTED` | |
| **LiveKit Evaluation** | | | |
| 6.12 | Benchmark Pipecat+Telnyx vs LiveKit at 30-50 concurrent | `NOT STARTED` | If consistently >20 |
| 6.13 | Document decision in `docs/livekit-evaluation.md` | `NOT STARTED` | |
| **Load Testing** | | | |
| 6.14 | Simulate 10/20/30/40/50 concurrent calls | `NOT STARTED` | |
| 6.15 | Measure latency degradation curve | `NOT STARTED` | |
| 6.16 | Document scaling thresholds | `NOT STARTED` | "Add second GPU at X" |

---

## Success Milestones

| Milestone | Definition | Target Phase | Status |
|---|---|---|---|
| Models validated | LLM, TTS, turn detector pass Spanish benchmarks | 0 | `DONE` |
| First call | AI answers phone, converses in Spanish, stores transcript | 1 | `PARTIAL` — conversation works at <1s latency, storage pending |
| Multi-agent | 5+ agents with KB handling calls | 3 | `NOT STARTED` |
| Optimized | p50 <1,000ms over 100 test calls | 4 | `EARLY` — ~830ms achieved in Phase 1, formal validation in Phase 4 |
| Observable | Grafana live, post-call analysis working | 5 | `NOT STARTED` |
| Production | 30 concurrent calls, overflow, batch complete | 6 | `NOT STARTED` |
| Cost target | Operating at <$0.03/min all-in | 6 | `NOT STARTED` |

---

## Blockers & Decisions Log

| Date | Item | Status | Resolution |
|---|---|---|---|
| 2026-03-22 | Need GPU server for Phase 0 benchmarks | `RESOLVED` | TensorDock RTX 4090 KVM. IP: 206.168.83.248. ~$3-4 spent. |
| 2026-03-22 | vLLM V1 engine crashes on TensorDock | `RESOLVED` | Downgraded to vLLM 0.6.6 (V0). |
| 2026-03-22 | LLM model selection | `RESOLVED` | **Qwen 2.5 7B AWQ.** |
| 2026-03-22 | STT fallback model | `RESOLVED` | **whisper-large-v3-turbo** (NOT distil-large-v3 — English only). |
| 2026-03-23 | TTS engine for Spanish | `RESOLVED` | **Kokoro, em_alex placeholder.** Custom voice generation deferred. XTTS/Fish too slow for real-time. |
| 2026-03-23 | HTTP audio player | `RESOLVED` | Port 8765 closed, server killed. Review complete. |
| 2026-03-22 | LLM latency much better than planned | `INFO` | TTFT 130ms @20 concurrent vs planned 500-800ms. Groq overflow threshold ~40-60. |
| 2026-03-22 | Fish S2-Pro uses 22GB VRAM | `INFO` | Cannot coexist with LLM. Only viable offline. |
| 2026-03-23 | Telnyx Mexico numbers | `RESOLVED` | Mexico only has toll-free at $20/month. Bought US local +12678840093 at $1/month instead. |
| 2026-03-23 | Turn detector for Spanish | `RESOLVED` | Pipecat Smart Turn v3 auto-loaded and working on real calls. |
| 2026-03-23 | Custom voice generation | `OPEN` | Kokoro em_alex is placeholder. Need to generate custom Mexican Spanish voices (post-Phase 1). |
| 2026-03-23 | First phone conversation achieved | `INFO` | Full conversation: greeting → problem diagnosis → router reset → resolution → goodbye. ~10 turns. |
| 2026-03-23 | Qwen Chinese code-switching | `MITIGATED` | Qwen 2.5 switches to Chinese mid-response. SpanishOnlyFilter strips CJK before TTS. System prompt says "NUNCA uses otro idioma." Underlying issue: model weakness. |
| 2026-03-23 | Kokoro TTS latency in pipeline | `RESOLVED` | Root cause: Pipecat SENTENCE mode buffers until period. Fix: comma→period trick in SpanishOnlyFilter. TTFB 800-2353ms → **389-554ms**. |
| 2026-03-23 | Garbled Spanish pronunciation | `RESOLVED` | Space-fixing regex in SpanishOnlyFilter: inserts spaces before ¿¡, after .!?,;: and at camelCase boundaries. Combined with SENTENCE mode, pronunciation is good. |
| 2026-03-24 | Audit fixes C1-C5, C9-C10 applied | `RESOLVED` | TTS mode configurable via env, hardcoded IP removed, engine.dispose on shutdown, agent fallback logged, DB pool tuned, async MinIO wrapper. |
| 2026-03-24 | TextAggregationMode.WORD doesn't exist | `RESOLVED` | Only SENTENCE and TOKEN in Pipecat 0.0.106. Removed WORD from mode_map. |
| 2026-03-24 | TOKEN mode: fast but unrecognizable | `INFO` | 123ms TTFB but Kokoro gets sub-word fragments ("Con", "esa", "pod") — too small for Spanish phonemization. SENTENCE + comma→period is the sweet spot. |
| 2026-03-24 | Prosody refinement needed | `OPEN` | Kokoro needs slightly more pause after periods and commas. Tune voice speed or add silence frames. Minor polish. |
| 2026-03-23 | Pipecat v0.0.106 API changes | `INFO` | Many breaking changes from older docs. All resolved. |
| 2026-03-24 | TensorDock GPU running | `INFO` | Instance running at $0.35/hr. Stop when done. |
