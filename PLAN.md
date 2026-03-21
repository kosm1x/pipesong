# Pipesong — Technical Plan

## 1. Project Goal

Build a cost-efficient, low-latency voice AI engine capable of handling hundreds to thousands of inbound phone calls with topic-trained AI agents. The system must achieve human-level conversation speed — under 800ms from end-of-speech to first response audio.

**This is not a SaaS platform.** It is a voice engine with an API. Agents are configured programmatically. There is no visual flow builder, no multi-tenant dashboard, no enterprise compliance layer. Every design decision optimizes for two things: **cost per minute** and **response latency**.

### Target Operating Profile

| Metric | Target |
|---|---|
| Concurrent calls | 30-50 (burst to 100+) |
| Monthly call volume | 100,000-400,000 minutes |
| Response latency (p50) | <800ms |
| Response latency (p95) | <1,200ms |
| Cost per minute | <$0.02 (vs Retell's $0.07-0.31) |
| Languages | Spanish + English |
| Agent spawn time | <60 seconds (API call + KB indexing) |

---

## 2. Cost Analysis

This is the foundation of every technology decision.

### Per-Minute Cost Comparison at 120,000 min/month

| Component | Retell AI | All Cloud | Hybrid (target) |
|---|---|---|---|
| Telephony | included | $840 | $840 |
| STT | included | $516 (Deepgram) | $516 (Deepgram) |
| LLM | included | $1,440 (GPT-4o-mini) | $0 (local) |
| TTS | included | $1,800 (ElevenLabs) | $0 (local Kokoro) |
| GPU servers | $0 | $0 | $800 (2× RTX 4090) |
| Infrastructure | $0 | $0 | $100 (VPS, Redis) |
| **Monthly total** | **$8,400-36,000** | **$4,596** | **$2,256** |
| **Per minute** | **$0.07-0.30** | **$0.038** | **$0.019** |

**Where the savings come from:**
- LLM: local vLLM saves ~$1,440/month vs GPT-4o-mini
- TTS: local Kokoro saves ~$1,800/month vs ElevenLabs
- STT: Deepgram stays cloud — local Whisper costs roughly the same in GPU time but adds 500-1000ms latency. Not worth the tradeoff.

**Why not fully local?** Self-hosted STT (faster-whisper) costs ~$400-600/month in GPU share and adds 500-1500ms of latency. Deepgram at $516/month for 120K minutes delivers 150-300ms streaming latency. The latency win is worth the ~$100/month difference. When local streaming STT matures (Moonshine, Ultravox), revisit this.

### Cost Scaling

| Volume | Telephony | Deepgram | GPU Servers | Total | Per min |
|---|---|---|---|---|---|
| 50,000 min/mo | $350 | $215 | $600 | $1,165 | $0.023 |
| 120,000 min/mo | $840 | $516 | $800 | $2,156 | $0.018 |
| 360,000 min/mo | $2,520 | $1,548 | $1,600 | $5,668 | $0.016 |

GPU costs are semi-fixed (step function when adding servers). As volume grows, per-minute cost drops.

---

## 3. Latency Architecture

Human turn-taking gap is 200-500ms. Phone conversations feel natural under 800ms. Above 1,200ms, the lag is consciously noticeable.

### Sequential Pipeline (Naive — What Most Projects Do)

```
[user speaks] → [silence] → [STT complete] → [LLM complete] → [TTS complete] → [play]
                  500ms         300ms             500ms            200ms           50ms
                                                                          Total: 1,550ms
```

This is too slow. Every open-source voice agent project that chains STT → LLM → TTS sequentially lands at 1.5-2.5 seconds.

### Overlapped Pipeline (Target Architecture)

```
Timeline (ms):  0    100   200   300   400   500   600   700   800
                |     |     |     |     |     |     |     |     |
VAD endpoint:   [====]                                          ← 200ms aggressive
STT streaming:  [===========]                                   ← processing during VAD silence
                      ↓ interim transcript
LLM prefill:          [=====]                                   ← start on partial transcript
                            ↓ STT final
LLM correction:             [===]                               ← adjust if transcript changed
                                ↓ first sentence
TTS streaming:                  [====]                          ← Kokoro, 100ms TTFB
                                     ↓ first audio chunk
Audio plays:                          [====→                    ← user hears response
                                                          Total: ~700-900ms
```

**Key techniques:**

1. **Speculative STT** — start STT processing during VAD endpoint silence (the 200ms where we're deciding if the user is done). By the time VAD confirms, STT has a head start.

2. **LLM on interim transcript** — when STT emits a partial/interim result, feed it to the LLM immediately. Don't wait for the final transcript. If the final transcript differs, inject a correction into the context. Most of the time, the interim is close enough that the LLM response doesn't change.

3. **Sentence-level TTS streaming** — the LLM generates tokens. As soon as a sentence boundary is detected (period, question mark, or pause token), that sentence is sent to Kokoro TTS immediately. TTS generates audio while the LLM is still producing the next sentence.

4. **Pre-cached responses** — for high-frequency agent utterances ("Can I have your name?", "Let me check that for you", "One moment please"), pre-generate the TTS audio at agent creation time. Play instantly with 0ms TTS latency.

5. **Warm models** — all models stay loaded in GPU memory. Zero cold-start. vLLM's continuous batching means adding a new request has near-zero overhead.

### Latency Budget (Target)

```
Component              | Target   | Method
-----------------------|----------|------------------------------------------
VAD endpoint           | 200ms    | Aggressive tuning + turn detector
STT                    | 200ms    | Deepgram streaming (overlapped with VAD)
LLM first token        | 250ms    | vLLM, Qwen 7B, speculative on interim
TTS first byte         | 100ms    | Kokoro streaming
Network + overhead     | 50ms     | Local GPU, minimal hops
-----------------------|----------|------------------------------------------
TOTAL                  | 800ms    | With overlap, not additive
Actual (overlapped)    | 700ms    | STT+LLM overlap saves ~200ms
```

---

## 4. System Architecture

### Core Principle: Separate Model Serving from Agent Logic

Agent workers are CPU-only processes that handle call logic. Model inference runs on shared GPU servers. This means:
- Agent workers scale horizontally on cheap CPU instances
- GPU capacity is shared efficiently across all concurrent calls via batching
- Adding call capacity = add more CPU workers (trivial)
- Adding inference capacity = add more GPU (when needed)

### Architecture Diagram

```
Telnyx SIP Trunk ($0.007/min, handles PSTN)
  │
  │ SIP/RTP
  ▼
LiveKit SIP Service (SIP termination, DTMF, transfers)
  │
  │ Opus/WebRTC
  ▼
LiveKit Server (room management, dispatch, Redis-backed)
  │
  │ Job dispatch
  ▼
┌──────────────────────────────────────────────────────┐
│  Agent Workers (CPU-only, N processes)                │
│                                                       │
│  ┌─────────────────────────────────────────────┐     │
│  │  Per-Call Pipeline (LiveKit AgentSession)    │     │
│  │                                              │     │
│  │  Audio In → Silero VAD → Turn Detector       │     │
│  │         → Deepgram STT (cloud, streaming)    │     │
│  │         → Agent Router (phone# → config)     │     │
│  │         → RAG retrieval (pgvector)            │     │
│  │         → LLM (→ vLLM server)                │     │
│  │         → TTS (→ Kokoro server)              │     │
│  │         → Audio Out                           │     │
│  └─────────────────────────────────────────────┘     │
│                                                       │
│  Worker 1 (5-10 calls)  Worker 2  ...  Worker N      │
└──────────────────────────────────────────────────────┘
         │                     │
         ▼                     ▼
┌──────────────────────────────────────────┐
│  GPU Server(s) — Shared Model Services   │
│                                           │
│  vLLM (Qwen 2.5 7B)    Kokoro TTS       │
│  OpenAI-compatible API   HTTP API         │
│  Continuous batching     ~0.5 GB VRAM     │
│  ~8 GB VRAM                               │
│                                           │
│  Turn Detector (CPU)    Silero VAD (CPU)  │
└──────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────┐
│  Data Layer                               │
│                                           │
│  PostgreSQL          Redis        MinIO   │
│  agents, calls,      state,       call    │
│  transcripts,        dispatch,    recordings │
│  pgvector (RAG)      cache                │
└──────────────────────────────────────────┘
```

### Concurrency Model

| Concurrent Calls | Agent Workers | GPU (LLM) | GPU (TTS) | Notes |
|---|---|---|---|---|
| 1-10 | 2 workers | 1× RTX 4090 | shared | Comfortable. Single server. |
| 10-30 | 4 workers | 1× RTX 4090 | shared | vLLM batching handles it. |
| 30-50 | 6-8 workers | 2× RTX 4090 | 1× shared | Split LLM across 2 GPUs. |
| 50-100 | 10-15 workers | 2-4× RTX 4090 | 1× shared | Kokoro is never the bottleneck (0.5GB). |
| 100+ | Scale workers | Add GPU nodes | shared | LiveKit clustering distributes load. |

**The bottleneck is always the LLM.** STT is cloud (Deepgram, unlimited concurrency). TTS is Kokoro (tiny, fast). VAD and turn detection are CPU. Only LLM inference requires significant GPU per concurrent call, and vLLM's continuous batching amortizes this well.

---

## 5. Technology Decisions

### 5.1 Framework: LiveKit Agents

At 30-50+ concurrent calls, infrastructure matters:

| Need | LiveKit Provides |
|---|---|
| SIP termination for 50+ simultaneous sessions | LiveKit SIP service (Go, high-concurrency) |
| Route incoming calls to available workers | Dispatch rules + job assignment |
| Scale workers horizontally | Agent server registers via Redis, auto-dispatch |
| Zero-downtime deploys | Graceful drain (SIGTERM → finish active calls → shutdown) |
| DTMF, call transfer | Built into SIP service |
| Call queuing on overload | Room-based model, backpressure |

Without LiveKit, you'd build all of this from scratch on top of Pipecat. Not worth it at this scale.

### 5.2 STT: Deepgram Nova-3

| Factor | Deepgram | Local faster-whisper |
|---|---|---|
| Latency | 150-300ms (streaming) | 800-1500ms (chunked) |
| Cost at 120K min/mo | $516 | ~$400-600 (GPU share) |
| Concurrency | Unlimited (cloud) | Limited by GPU |
| Languages | 40+ (strong Spanish) | 99 (strong Spanish) |
| Maintenance | Zero | Model updates, GPU management |

Same cost, 5-10× better latency, zero maintenance. Deepgram until local streaming STT catches up.

**Migration path:** When Moonshine, distil-whisper streaming, or Ultravox mature, swap Deepgram for local. The STT interface is the same (streaming audio in, text out). No architecture change needed.

### 5.3 LLM: Local via vLLM

| Factor | Decision |
|---|---|
| Server | vLLM (continuous batching, OpenAI-compatible API) |
| Model | Qwen 2.5 7B-Instruct (strong Spanish, good tool calling) |
| Quantization | AWQ 4-bit (~5 GB VRAM) for max concurrency |
| Fallback | Groq API (fast, cheap) if local GPU is saturated |
| VRAM | ~5-8 GB per model instance |

vLLM over Ollama because continuous batching handles 20-40 concurrent requests on a single GPU efficiently. Ollama processes requests sequentially.

**Model selection criteria for voice agents:**
- First-token latency <300ms (rules out 70B+ models)
- Strong Spanish + English
- Reliable function calling (for tools)
- Fits in 8 GB VRAM quantized (rules out 14B+ on single GPU)

Qwen 2.5 7B hits all of these. Benchmark against Llama 3.1 8B and Gemma 2 9B before committing.

### 5.4 TTS: Kokoro (Local)

| Factor | Value |
|---|---|
| TTFB | 50-150ms (GPU), 200-500ms (CPU) |
| VRAM | ~0.5 GB |
| Quality | MOS 3.8-4.2 (good for phone, where 8kHz codec is the quality ceiling) |
| Languages | English, Japanese, Chinese, Korean, French + community voices |
| Spanish | Community voices available, quality TBD — benchmark required |
| Voice cloning | Not supported (pre-trained styles only) |
| License | Apache 2.0 |
| Streaming | Yes, chunk-by-chunk as text arrives |

At 8kHz telephony quality (G.711 codec), the perceptual difference between Kokoro and ElevenLabs is minimal. The phone codec is the bottleneck, not the TTS model.

**Risk: Spanish voice quality.** Kokoro's Spanish support is community-contributed. If quality is insufficient:
- Fish Speech: good Spanish, Apache 2.0, 200-400ms TTFB, but ~4-6 GB VRAM
- F5-TTS: good quality, 300-600ms TTFB
- ElevenLabs API fallback: $0.015/min, excellent quality, adds cloud dependency

### 5.5 Telephony: Telnyx + LiveKit SIP

| Choice | Rationale |
|---|---|
| Telnyx over Twilio | ~50% cheaper ($0.007/min vs $0.013/min). TeXML is TwiML-compatible. Good Mexico coverage. |
| LiveKit SIP over raw PBX | Handles SIP termination, dispatch, DTMF, transfers without FreeSWITCH/Asterisk complexity. |
| No PBX needed | LiveKit SIP replaces the PBX for AI agent use cases. Add FreeSWITCH only if we need IVR trees or 500+ concurrent sessions. |

### 5.6 Turn Detection

LiveKit's transformer model (fine-tuned Qwen2.5-0.5B for end-of-utterance prediction):
- Runs on CPU (50-160ms inference)
- 14 languages including Spanish
- Reduces false interruptions by ~85% vs VAD alone
- Open weights (Hugging Face: `livekit/turn-detector`)
- Dynamically adjusts VAD silence timeout based on linguistic context

Combined with Silero VAD (<10ms, CPU) for speech boundary detection.

### 5.7 Knowledge Base: pgvector in PostgreSQL

No separate vector database. pgvector handles:
- Document chunk storage with embeddings
- Cosine similarity search at <50ms for 100K chunks
- Per-agent knowledge isolation via foreign key
- Scales well within PostgreSQL for our document volumes

Embedding model: `text-embedding-3-small` (OpenAI, $0.02/1M tokens) or local `all-MiniLM-L6-v2` (free, slightly lower quality).

### 5.8 Infrastructure Stack

| Service | Purpose | Resource |
|---|---|---|
| LiveKit Server | SFU, room management, dispatch | CPU, 2-4 GB RAM |
| LiveKit SIP | SIP termination, PSTN bridge | CPU, 1-2 GB RAM |
| Redis | LiveKit state, caching, queues | CPU, 1-2 GB RAM |
| PostgreSQL + pgvector | Agents, calls, transcripts, embeddings | CPU, 4-8 GB RAM |
| MinIO | Call recordings, uploaded documents | Disk, minimal RAM |
| vLLM | LLM inference server | GPU, 8-16 GB VRAM |
| Kokoro server | TTS inference server | GPU, 0.5-1 GB VRAM |
| Agent workers | Call handling, pipeline logic | CPU only, 1 GB RAM each |
| FastAPI server | Management API, webhooks | CPU, 512 MB RAM |

**Minimum hardware for 30 concurrent calls:**
- 1× GPU server: 2× RTX 4090, 64 GB RAM, 16-core CPU
- Or equivalent cloud: RunPod/Vast.ai ($0.30-0.50/hr per 4090)

---

## 6. Agent Model

### What an Agent Is

An agent is a configuration record, not a running process. When a call arrives, the system loads the agent config and initializes a pipeline with those parameters.

```
Agent {
  id: uuid
  name: string
  phone_number: string                    # Telnyx number assigned
  system_prompt: string                   # Core personality and instructions
  language: "es" | "en" | "multi"
  voice: string                           # Kokoro voice ID
  voice_speed: float (0.5-2.0)
  llm_model: string                       # "qwen2.5-7b" or "groq/llama-3.1-8b"
  llm_temperature: float (0-1)
  tools: Tool[]                           # Functions the agent can call
  knowledge_base_id: uuid | null          # RAG document collection
  max_call_duration: int (seconds)        # Default: 600 (10 min)
  silence_timeout: int (seconds)          # End call after N seconds silence
  success_criteria: string | null         # For post-call analysis
  webhook_url: string | null              # Call lifecycle events
  dynamic_variables: Record<string, string>  # Default variables
  created_at: timestamp
}
```

### Spawning a New Agent

One API call. Takes <60 seconds including KB indexing.

```
POST /agents
{
  "name": "Soporte Técnico MiEmpresa",
  "system_prompt": "Eres un agente de soporte técnico para MiEmpresa...",
  "language": "es",
  "voice": "kokoro_es_male_1",
  "tools": [
    {
      "name": "check_ticket_status",
      "description": "Check the status of a support ticket",
      "endpoint": "https://api.miempresa.com/tickets/{ticket_id}",
      "method": "GET"
    },
    {
      "name": "create_ticket",
      "description": "Create a new support ticket",
      "endpoint": "https://api.miempresa.com/tickets",
      "method": "POST",
      "parameters": { "subject": "string", "description": "string", "priority": "low|medium|high" }
    }
  ],
  "knowledge_base": {
    "documents": ["https://miempresa.com/faq", "/path/to/manual.pdf"],
    "auto_refresh": true
  }
}
```

Response includes the assigned phone number. Agent is live immediately.

### Conversation Flows (Code-Defined)

No visual builder. Flows are Python state machines defined in agent config or code.

```python
# Example: appointment booking flow
flow = ConversationFlow(
    states={
        "greeting": State(
            prompt="Greet the caller and ask how you can help",
            transitions=[
                Transition(condition="caller wants appointment", target="collect_info"),
                Transition(condition="caller has question", target="answer_question"),
            ]
        ),
        "collect_info": State(
            prompt="Ask for their name, preferred date, and time",
            tools=["check_availability"],
            transitions=[
                Transition(condition="info collected", target="confirm"),
            ]
        ),
        "confirm": State(
            prompt="Confirm the appointment details and book it",
            tools=["book_appointment"],
            transitions=[
                Transition(condition="confirmed", target="farewell"),
                Transition(condition="wants to change", target="collect_info"),
            ]
        ),
        "farewell": State(prompt="Thank them and end the call", end=True),
        "answer_question": State(
            prompt="Answer using knowledge base",
            use_knowledge_base=True,
            transitions=[
                Transition(condition="question answered", target="farewell"),
                Transition(condition="needs appointment", target="collect_info"),
            ]
        ),
    }
)
```

This replaces Retell's visual flow builder. Faster to build, easier to version control, more flexible. A visual editor can be added later if needed — the flow model supports it.

---

## 7. Retell Feature Coverage

What we're building, what we're skipping, and why.

### Building (Critical Path)

| Feature | Priority | Notes |
|---|---|---|
| Single-prompt agents | Phase 1 | System prompt → agent. Simplest path. |
| Inbound call handling | Phase 1 | Phone number → agent routing |
| Outbound calls via API | Phase 2 | `POST /calls` to trigger call |
| Function calling (sync + async) | Phase 2 | Tools defined per agent, HTTP webhooks |
| Dynamic variables | Phase 2 | `{{variable}}` in prompts, set per-call |
| Knowledge base (RAG) | Phase 3 | Document upload, auto-chunking, per-agent |
| Conversation flows (code-defined) | Phase 4 | State machine with LLM-evaluated transitions |
| Call transfer (cold) | Phase 2 | SIP REFER via LiveKit |
| Call transfer (warm) | Phase 4 | Agent-assisted handoff |
| DTMF handling | Phase 2 | Detect and send button presses |
| Call recording + transcription | Phase 1 | Stored in MinIO + PostgreSQL |
| Post-call analysis | Phase 5 | LLM summary, sentiment, success/failure, custom extractors |
| Webhooks | Phase 2 | call_started, call_ended, call_analyzed |
| Latency metrics per call | Phase 5 | e2e, STT, LLM, TTS breakdown |
| Batch outbound calling | Phase 6 | CSV list, concurrent dispatch |
| Voicemail detection | Phase 6 | Analyze initial audio on outbound |

### Building Later (Useful but Not Critical)

| Feature | When | Notes |
|---|---|---|
| Web playground (WebRTC test) | After Phase 4 | Useful for testing without burning phone minutes |
| Monitoring dashboards | After Phase 5 | Grafana + Prometheus, not custom React charts |
| Silence reminders | Phase 4 | "Are you still there?" after N seconds |
| Backchannel ("uh-huh") | Phase 4 | Configurable filler sounds during user speech |
| Voice cloning | When needed | Fish Speech or F5-TTS, not Kokoro |
| Interruption sensitivity tuning | Phase 4 | Per-agent configurable threshold |
| Boosted keywords for STT | Phase 3 | Send domain terms to Deepgram |
| URL auto-crawl for KB | Phase 3 | Re-index every 24h |

### Not Building

| Retell Feature | Reason |
|---|---|
| Visual flow builder (React Flow) | Code-defined flows are faster to build and more flexible. Add UI only if non-developers need to create flows. |
| React dashboard | API-first. Use Grafana for monitoring. Admin tasks via API/CLI. |
| Multi-tenant workspaces | Single operator (you). Add tenant isolation only if reselling. |
| RBAC (admin/developer/viewer) | Single operator. |
| Chat widget / SMS / omnichannel | Voice-first. Add channels when voice is solid. |
| Simulation testing harness | Test with real calls during development. Add automated testing when agent count justifies it. |
| QA cohorts with AI scoring | Post-call analysis covers this. Formal QA is premature. |
| SSO / SOC2 / HIPAA | Not relevant until selling to enterprises. |
| Ambient sounds (coffee shop, etc.) | Trivial audio mixing, zero priority. |
| A/B testing | Test manually, compare call analysis results. |
| PII scrubbing | Build when handling sensitive data. |
| Agent versioning with diff | Git handles this for code-defined agents. |
| Branded caller ID | Telnyx/carrier feature, not application-level. |
| Guardrails (content moderation) | LLM system prompt handles this. Add dedicated guardrails if abuse patterns emerge. |

---

## 8. Build Phases

### Phase 1 — First Phone Call (2 weeks)

**Goal:** Call a number, AI answers, have a conversation, hang up. Record everything.

**Infrastructure:**
1. Docker Compose: LiveKit Server + SIP service + Redis + PostgreSQL + MinIO
2. Telnyx account: SIP trunk configuration + first phone number
3. GPU server: vLLM (Qwen 2.5 7B) + Kokoro TTS serving

**Agent pipeline:**
4. LiveKit Agent worker with `AgentSession`
5. Deepgram STT plugin (streaming)
6. vLLM LLM via OpenAI-compatible plugin
7. Kokoro TTS plugin (streaming, sentence-level)
8. Silero VAD + LiveKit turn detector

**API + storage:**
9. FastAPI: `POST /agents` (create), `GET /agents` (list)
10. PostgreSQL schema: agents, calls, transcripts
11. Call recording pipeline: audio → MinIO, transcript → PostgreSQL

**Exit criteria:** Dial a phone number, have a 3-minute conversation in Spanish, verify transcript is stored correctly.

---

### Phase 2 — Multi-Agent + Tools (2 weeks)

**Goal:** Multiple agents on different numbers, each with their own personality and tools.

**Agent configuration:**
1. Full agent model: system prompt, voice, LLM, temperature, language, tools, variables
2. Phone number → agent routing via LiveKit dispatch rules
3. Dynamic variables: `{{name}}`, `{{company}}`, etc. in prompts, set per-call

**Function calling:**
4. Tool definition per agent (name, description, parameters, HTTP endpoint, method)
5. Sync execution: agent waits for result, speaks about it
6. Async execution: agent speaks filler ("Let me check...") while tool runs
7. HMAC signature on outgoing webhook requests

**Telephony:**
8. Outbound call API: `POST /calls` with `to`, `from`, `agent_id`
9. Cold call transfer via LiveKit `TransferSIPParticipant`
10. DTMF detection and forwarding
11. Inbound webhook: dynamic agent selection based on caller number or time of day

**Webhooks:**
12. `call_started`, `call_ended` events to agent's `webhook_url`
13. Webhook payload: call_id, agent_id, phone numbers, duration, transcript

**Exit criteria:** Two different agents on two different numbers. Agent A books appointments via API. Agent B answers product questions. Both handle tool calls correctly.

---

### Phase 3 — Knowledge Base (1-2 weeks)

**Goal:** Agents answer questions from uploaded documents accurately.

**Document pipeline:**
1. Upload API: accept PDF, DOCX, TXT, MD, CSV, HTML
2. Document processing: extract text → chunk (512 tokens, 50 token overlap) → embed → store in pgvector
3. Embedding: OpenAI `text-embedding-3-small` ($0.02/1M tokens) or local `all-MiniLM-L6-v2`
4. Per-agent KB assignment

**Retrieval during calls:**
5. On each user turn: embed latest utterance → cosine similarity against agent's KB → inject top-3 chunks into LLM context
6. Retrieval latency target: <50ms
7. Configurable: chunk count (1-10), similarity threshold

**Knowledge management:**
8. URL sources: fetch and index web pages
9. Auto-refresh: re-crawl URLs every 24h via background worker
10. Boosted keywords: send domain-specific terms to Deepgram for better recognition

**Exit criteria:** Upload a 20-page product manual. Call the agent. Ask 10 questions from the manual. Agent answers 8+ correctly with specific details from the document.

---

### Phase 4 — Latency Optimization + Conversation Flows (2-3 weeks)

**Goal:** Sub-800ms p50 latency. Structured multi-step conversations.

**Pipeline overlap (the critical work):**
1. Speculative STT: begin processing audio during VAD endpoint silence
2. LLM on interim transcripts: start generation on Deepgram's interim results
3. Sentence-level TTS streaming: detect sentence boundaries in LLM output, stream each to Kokoro immediately
4. Pre-cached responses: generate TTS for common phrases at agent creation time
5. Connection keep-alive: persistent WebSocket to Deepgram, persistent HTTP/gRPC to vLLM and Kokoro

**Turn-taking refinement:**
6. Per-agent interruption sensitivity (configurable threshold)
7. Backchannel generation ("uh-huh", "mmm") during user speech
8. Silence reminders ("Are you still there?") after configurable timeout
9. Block interruptions during critical agent speech (tool result delivery)

**Conversation flows:**
10. Flow engine: state machine with states, transitions, per-state tools
11. Transition evaluation: equation conditions (variable-based) + prompt conditions (LLM-evaluated)
12. Per-state LLM context: each state has its own prompt, injected alongside the global system prompt
13. Variable extraction: LLM extracts structured data into flow variables
14. Warm call transfer: create second SIP session, agent provides context, bridge

**Latency measurement:**
15. Instrument every pipeline stage: VAD, STT, LLM, TTS timestamps per turn
16. Log p50/p90/p95/p99 per agent
17. Expose via API: `GET /calls/{id}/latency`

**Exit criteria:** p50 latency under 800ms over 100 test calls. A 5-state appointment booking flow completes successfully.

---

### Phase 5 — Call Analysis + Monitoring (2 weeks)

**Goal:** Understand how agents perform. Catch failures.

**Post-call analysis:**
1. On `call_ended`: send transcript to LLM (can use a cheaper/faster model)
2. Extract: summary, user sentiment (positive/negative/neutral), success/failure (per agent's criteria)
3. Custom extractors: define per-agent fields to extract (e.g., "ticket_number: string", "issue_resolved: boolean")
4. Store analysis in PostgreSQL alongside call record
5. `call_analyzed` webhook event

**Monitoring:**
6. Prometheus metrics: calls_total, calls_active, call_duration, latency_e2e, latency_stt, latency_llm, latency_tts, errors_total
7. Grafana dashboards: call volume, latency trends, success rate, per-agent breakdown
8. Alerting: Grafana alerts on latency p95 > threshold, error rate > threshold

**API:**
9. `GET /calls` with filters (agent, date range, success, sentiment)
10. `GET /calls/{id}` with full transcript, analysis, latency breakdown, recording URL
11. `GET /agents/{id}/stats` — aggregated metrics for an agent

**Exit criteria:** Grafana dashboard shows real-time call volume and latency. Post-call analysis correctly identifies success/failure on 90%+ of calls.

---

### Phase 6 — Scale + Production Hardening (2 weeks)

**Goal:** Handle 50+ concurrent calls reliably. Outbound batch calling.

**Scaling:**
1. Multi-worker deployment: N agent workers behind LiveKit dispatch
2. Graceful drain: SIGTERM → finish active calls → shutdown (zero-downtime deploys)
3. GPU fallback: if vLLM queue depth > threshold, route to Groq API for that call
4. Health checks: LiveKit agent health, vLLM health, Kokoro health, Deepgram connectivity
5. Auto-restart on worker crash (systemd/Docker restart policy)

**Batch calling:**
6. `POST /batch-calls` with CSV (phone numbers + per-row dynamic variables)
7. Concurrency control: max N simultaneous outbound calls
8. Rate limiting: respect Telnyx CPS limits (16/sec)
9. Status tracking: pending → in_progress → completed/failed per row

**Reliability:**
10. Retry logic: if STT/TTS/LLM fails mid-call, attempt recovery before dropping
11. Call queue: if all workers are busy, hold inbound calls with music (LiveKit room audio)
12. Voicemail detection on outbound: analyze initial audio, skip if machine answers

**Load testing:**
13. Simulate 50 concurrent calls, measure latency degradation
14. Identify bottleneck (likely LLM), document scaling thresholds

**Exit criteria:** 50 concurrent calls sustained for 30 minutes with p95 latency < 1,200ms. Batch call of 200 numbers completes without manual intervention.

---

## 9. Future Directions (Not Planned, Not Scoped)

These are potential future phases if the core engine proves out:

| Direction | Trigger |
|---|---|
| **Local streaming STT** (Moonshine, Ultravox) | When a local model achieves <300ms streaming latency with acceptable WER in Spanish |
| **Speech-to-speech models** | When Ultravox or Moshi-like models handle Spanish + tool calling reliably. Collapses STT+LLM into one step for <500ms total latency |
| **Web playground** | When testing agents without phone minutes becomes a pain point |
| **Visual flow builder** | When non-developers need to create flows (would use React Flow) |
| **SMS / WhatsApp channel** | When voice-only is insufficient for specific use cases |
| **Voice cloning** | When clients need branded voices. Fish Speech or F5-TTS |
| **Multi-tenant SaaS** | When reselling Pipesong as a service makes business sense |
| **On-premise GPU** | When monthly GPU rental exceeds hardware amortization (break-even: ~12-18 months) |

---

## 10. Technical Risks

| Risk | Impact | Probability | Mitigation |
|---|---|---|---|
| **Deepgram cost at scale** | $516/mo at 120K min. Acceptable but adds up. | Medium | Budget for it. Migrate to local STT when viable. STT interface is swappable. |
| **Kokoro Spanish voice quality** | May sound unnatural in Spanish | Medium | Benchmark early. Fish Speech fallback. ElevenLabs as cloud fallback ($0.015/min). |
| **LLM quality for voice** | 7B models may give shallow/incorrect answers on complex topics | Medium | RAG compensates. Cloud LLM fallback (Groq) for complex agents. Benchmark before deploying. |
| **vLLM under concurrent load** | Latency degrades at 30+ concurrent requests | Low-Medium | Continuous batching helps. Monitor queue depth. Groq fallback for overflow. Second GPU for scaling. |
| **LiveKit SIP edge cases** | Less mature than FreeSWITCH for complex telephony | Low | Telnyx WebSocket fallback for specific failure modes. FreeSWITCH only if SIP issues are persistent. |
| **Overlap pipeline complexity** | Speculative LLM on interim transcripts may produce incorrect responses | Medium | Validate: compare interim vs final transcript accuracy. Fall back to sequential if error rate > 5%. |
| **Turn detection in Spanish** | LiveKit's model trained primarily on English | Medium | Benchmark with Spanish test calls. Tune VAD parameters. Consider Pipecat's audio-based Smart Turn as alternative. |
| **GPU provider reliability** | Cloud GPU providers (RunPod, Vast.ai) have variable uptime | Medium | Dedicated server from Hetzner/OVH. Or reserve instances. Keep cloud LLM fallback warm. |

---

## 11. Success Metrics

| Milestone | Definition | Target Date |
|---|---|---|
| **First call** | AI answers a phone call and holds a conversation | Phase 1 end |
| **Multi-agent** | 5+ agents on different numbers, each topic-trained with KB | Phase 3 end |
| **Sub-second** | p50 latency < 800ms sustained | Phase 4 end |
| **Production** | 50 concurrent calls, monitored, analyzed, reliable | Phase 6 end |
| **Cost target** | Operating at < $0.02/min all-in | Phase 6 end |
