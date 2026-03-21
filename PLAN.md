# Pipesong — Technical Plan (v3)

## 1. Project Goal

Build a cost-efficient, low-latency voice AI engine capable of handling hundreds to thousands of inbound phone calls with topic-trained AI agents.

**This is not a SaaS platform.** It is a voice engine with an API. Agents are configured programmatically — as data, not code. Every design decision optimizes for two things: **cost per minute** and **response latency**.

### Target Operating Profile

| Metric | Target | Honest Range |
|---|---|---|
| Concurrent calls | 30-50 (burst to 100+) | 10-50 before needing second GPU |
| Monthly call volume | 100,000-400,000 minutes | Depends on concurrent capacity |
| Response latency (p50) | <1,000ms | 900-1,100ms realistic with overlap |
| Response latency (p95) | <1,500ms | Degrades under concurrent load |
| Cost per minute | <$0.03 all-in | $0.025-0.035 with operational overhead |
| Languages | Spanish + English | Spanish TTS quality is a risk — validated in Phase 0 |
| Agent spawn time | <60 seconds | Excluding large KB indexing |

### Why These Targets Changed From v2

The previous plan claimed <800ms p50 and <$0.02/min. Those numbers assumed:
- Overlapped pipeline with speculative LLM on interim transcripts (unreliable in practice — interim transcripts in Spanish are often wrong enough to produce bad responses)
- LLM latency at low concurrency (at 30 concurrent calls, vLLM first-token latency degrades from ~200ms to ~500-800ms)
- Zero operational overhead (no GPU redundancy, no monitoring, no fallback costs)

The revised targets are achievable with proven techniques. Still 3-10× cheaper than Retell ($0.07-0.31/min).

---

## 2. Cost Analysis

### Per-Minute Cost at 120,000 min/month (Honest)

| Component | Retell AI | Pipesong | Notes |
|---|---|---|---|
| Telephony | included | $840 | Telnyx at $0.007/min |
| STT | included | $516 | Deepgram Nova-3 at $0.0043/min |
| LLM | included | $0 | Local vLLM |
| TTS | included | $0 | Local Kokoro |
| GPU servers (primary) | $0 | $800 | 2× RTX 4090 dedicated |
| GPU fallback (cloud LLM) | $0 | $150 | Groq for overflow, ~10% of calls |
| Embeddings | $0 | $30 | OpenAI text-embedding-3-small for RAG |
| Infrastructure (VPS, Redis) | $0 | $100 | |
| Storage (recordings) | $0 | $50 | MinIO disk growth |
| Monitoring (Grafana) | $0 | $0 | Self-hosted |
| **Monthly total** | **$8,400-36,000** | **$2,486** | |
| **Per minute** | **$0.07-0.30** | **$0.021** | |

**Not included:** GPU redundancy for HA (+$400-800/month if needed). Engineering maintenance time. Telnyx international rates if callers are outside US ($0.015-0.80/min, not $0.007).

**Realistic all-in range: $0.025-0.035/min** depending on call mix, concurrency peaks, and fallback usage.

### Where the Savings Come From

| Component | Cloud Cost | Local Cost | Monthly Saving |
|---|---|---|---|
| LLM (GPT-4o-mini) | $1,440 | ~$200 (GPU share) | $1,240 |
| TTS (ElevenLabs) | $1,800 | ~$50 (GPU share) | $1,750 |
| STT (Deepgram) | $516 | $516 (keep cloud) | $0 |

**LLM and TTS are where self-hosting pays off.** STT stays cloud because local Whisper adds 500-1500ms for similar cost.

### Cost Scaling

| Volume | Telephony | Deepgram | GPU | Overhead | Total | Per min |
|---|---|---|---|---|---|---|
| 50K min/mo | $350 | $215 | $600 | $200 | $1,365 | $0.027 |
| 120K min/mo | $840 | $516 | $800 | $330 | $2,486 | $0.021 |
| 360K min/mo | $2,520 | $1,548 | $1,600 | $500 | $6,168 | $0.017 |

---

## 3. Architecture — Two Stages

### Why Two Stages

The previous plan committed to LiveKit (5 services) from day one. But:
- Telnyx already provides SIP termination, phone numbers, DTMF, call transfer, voicemail detection, and WebSocket audio streaming
- A single Python process with Pipecat handles 10-20 concurrent WebSocket connections
- LiveKit adds value only at 30+ concurrent calls where you need dispatch, clustering, and horizontal scaling

**Start simple. Graduate when you need to.**

### Stage 1: Pipecat + Telnyx (Phases 0-4)

For development and initial production (up to ~20 concurrent calls):

```
Telnyx (PSTN + SIP termination + phone numbers)
  │
  │ WebSocket (8kHz mulaw audio)
  ▼
Python App (Pipecat pipeline, FastAPI server)
  │
  ├─ Silero VAD + turn detector (CPU)
  ├─ Deepgram STT (cloud WebSocket, streaming)
  ├─ Agent Router (phone number → agent config from PostgreSQL)
  ├─ RAG retrieval (pgvector, <50ms)
  ├─ LLM (→ vLLM server, local GPU)
  └─ TTS (→ Kokoro server, local GPU)
       │
       ▼
  Audio back to Telnyx WebSocket → caller hears response

Storage: PostgreSQL + pgvector | MinIO (recordings)
```

**Services to run:** Python app, vLLM, Kokoro HTTP server, PostgreSQL, MinIO.
**No LiveKit, no Redis, no SIP service.** Telnyx handles all telephony.

### Stage 2: LiveKit (Phase 5+)

When concurrent calls exceed ~20 or you need features Telnyx WebSocket doesn't provide (SIP trunk flexibility, WebRTC browser testing, multi-node clustering):

```
Telnyx SIP Trunk
  │
  ▼
LiveKit SIP Service → LiveKit Server → Redis
  │
  ▼
Agent Workers (N processes, CPU-only)
  │
  ├─ Deepgram STT (cloud)
  ├─ Agent Router + RAG
  ├─ LLM (→ vLLM server)
  └─ TTS (→ Kokoro server)

Storage: PostgreSQL + pgvector | MinIO
```

**Migration path:** The agent pipeline code doesn't change — Pipecat has a `LiveKitTransport` that replaces the `TelnyxFrameSerializer`. The pipeline (STT → LLM → TTS) stays identical. You're swapping the transport layer, not rewriting the engine.

### Separated Model Serving (Both Stages)

Agent processes are CPU-only. Model inference runs on shared GPU servers:

```
GPU Server (2× RTX 4090, 24 GB VRAM each)
┌─────────────────────────────────────────┐
│  vLLM (Qwen 2.5 7B AWQ)               │
│  Port 8000, OpenAI-compatible API       │
│  Continuous batching, ~8 GB VRAM        │
│                                          │
│  Kokoro TTS HTTP Server                  │
│  Port 8001                               │
│  ~0.5 GB VRAM                            │
│                                          │
│  faster-whisper (fallback STT)           │
│  Port 8002, distil-large-v3             │
│  ~4 GB VRAM, activated only on          │
│  Deepgram failure                        │
│                                          │
│  Free VRAM: ~11 GB (headroom/scaling)    │
└─────────────────────────────────────────┘
```

### Concurrency vs Latency (Honest Numbers)

LLM is the bottleneck. vLLM continuous batching helps throughput but per-request latency degrades with batch size:

| Concurrent Calls | LLM First-Token (Qwen 7B AWQ) | Total Pipeline (p50) | Action |
|---|---|---|---|
| 1-5 | ~200ms | ~900ms | Comfortable |
| 5-15 | ~300ms | ~1,000ms | Normal operation |
| 15-25 | ~450ms | ~1,200ms | Approaching limit |
| 25-35 | ~600ms | ~1,400ms | Overflow to Groq |
| 35+ | Degrades further | >1,500ms | Need second GPU |

**Strategy:** Monitor vLLM queue depth. When it exceeds threshold (e.g., >15 pending), route new calls to Groq API ($0.003/min for Llama 3.1 8B) instead of local vLLM. Caller gets same quality, you pay a few cents, latency stays under control.

---

## 4. Latency Architecture

### Realistic Pipeline (What We're Actually Building)

Forget the speculative interim-transcript trick from v2. It sounds good on paper but Deepgram's Spanish interim transcripts are wrong often enough (~15-20% of utterances have meaningful differences between interim and final) that speculative LLM generation produces bad responses too frequently.

Instead, use **proven overlap techniques only:**

```
[user speaks] → [VAD detects silence]
                    │
                    ├─ Turn detector evaluates (50-160ms, CPU)
                    │   └─ "Is the user done?" → adjusts VAD timeout
                    │
                    ├─ Deepgram is already streaming and processing
                    │   └─ FINAL transcript arrives ~200ms after speech end
                    │
                    └─ LLM starts on FINAL transcript
                        │
                        ├─ First sentence generated (~300-500ms)
                        │   └─ Immediately sent to Kokoro TTS
                        │       └─ First audio chunk in ~100ms
                        │           └─ Caller hears first word
                        │
                        └─ Remaining sentences stream to TTS
                            while LLM continues generating
```

### Latency Budget (Honest)

```
Component              | Target   | Realistic  | Method
-----------------------|----------|------------|------------------------------------------
VAD endpoint           | 250ms    | 200-400ms  | Turn detector shortens/lengthens dynamically
Deepgram final         | 200ms    | 150-350ms  | Streaming; final arrives shortly after speech end
LLM first token        | 300ms    | 200-600ms  | vLLM; degrades with concurrency
LLM first sentence     | +200ms   | +100-400ms | Depends on sentence length
TTS first byte         | 100ms    | 50-200ms   | Kokoro streaming
Network + overhead     | 50ms     | 30-100ms   | Local GPU, minimal hops
-----------------------|----------|------------|------------------------------------------
TOTAL (sequential)     | 1,100ms  | 930-1,650ms|
With sentence overlap  | -200ms   | -100-300ms | TTS starts before LLM finishes
ACTUAL p50             |          | 900-1,100ms|
ACTUAL p95             |          | 1,200-1,500ms|
```

**This is honest.** It's not the 700ms from v2 and not the 1,550ms of a naive pipeline. It's the achievable middle ground with proven techniques.

### Optimization Techniques (Proven, Not Speculative)

1. **Sentence-level TTS streaming** — detect sentence boundaries in LLM output stream. Send each sentence to Kokoro immediately. TTS generates audio while LLM produces the next sentence. This overlaps LLM and TTS, saving 100-300ms.

2. **Pre-cached common responses** — generate TTS audio at agent creation time for phrases the agent will say frequently: greetings, confirmations, hold messages, farewells. Play instantly (0ms TTS). Identify candidates from post-call analysis after a few hundred calls.

3. **Warm connections** — persistent WebSocket to Deepgram (no per-call handshake), persistent HTTP to vLLM and Kokoro. Eliminates connection setup latency.

4. **Turn detector** — LiveKit's transformer model (works standalone, doesn't require LiveKit infrastructure) adjusts VAD silence timeout dynamically. Short timeout for clear turn-endings ("Thank you"), long timeout for mid-thought pauses ("I need to... um..."). Prevents both premature responses and unnecessary waiting.

5. **First-word priority** — configure LLM to start responses with short, contextual acknowledgments ("Claro", "Por supuesto", "Entendido") before the substantive answer. Caller hears something immediately while the full response generates.

### Future Latency Improvements (Not In Current Plan)

| Technique | Potential Saving | When Viable |
|---|---|---|
| Local streaming STT (Moonshine) | -100-200ms (eliminate Deepgram round-trip) | When Moonshine achieves <300ms with good Spanish WER |
| Speech-to-speech (Ultravox/Moshi) | -300-500ms (collapse STT+LLM) | When these models handle Spanish + function calling |
| Speculative LLM on interim transcript | -200-300ms | When Deepgram interim accuracy in Spanish reaches >95% |
| Custom turn-detection fine-tuned for Spanish | -50-100ms (better endpoint timing) | After collecting 1000+ call transcripts for training data |

---

## 5. Technology Decisions

### 5.1 Framework: Pipecat (Stage 1) → LiveKit (Stage 2)

**Phase 0-4: Pipecat**
- Python library, `pip install pipecat-ai`
- Built-in Telnyx WebSocket serializer
- Handles: VAD → STT → LLM → TTS pipeline, interruption handling, turn detection, sentence-level streaming
- No infrastructure to deploy (it's a library, not a service)
- Limitation: no built-in dispatch, scaling, or multi-node orchestration

**Phase 5+: LiveKit (if/when needed)**
- Pipecat has `LiveKitTransport` — same pipeline code, different transport
- LiveKit adds: SIP termination (trunk flexibility), job dispatch, Redis clustering, graceful drain
- Trade-off: 3 extra services (LiveKit Server, SIP service, Redis) for scaling primitives

**Decision criteria for migration:** When you consistently run >20 concurrent calls, or when you need SIP trunk flexibility beyond Telnyx, or when you need WebRTC browser testing.

### 5.2 STT: Deepgram Nova-3 (Primary) + Local faster-whisper (Fallback)

**Primary: Deepgram**
- 150-300ms streaming latency
- $0.0043/min ($516/month at 120K min)
- Strong Spanish support
- Zero maintenance

**Fallback: Local faster-whisper (distil-large-v3)**
- Activated when: Deepgram is down, Deepgram is slow (>500ms), or network issues
- ~800-1500ms latency (chunked, not streaming)
- ~4 GB VRAM on shared GPU
- Caller experiences degraded but functional service instead of dropped call

**Why not local-only?** At 120K min/month, local STT GPU cost (~$400-600/month) is similar to Deepgram ($516/month) but adds 500-1000ms latency. The cost savings don't exist; only the latency penalty. Deepgram is the correct choice until a local model achieves <300ms streaming.

**Why fallback matters:** Deepgram is a single point of failure. Every call depends on it. A 30-minute Deepgram outage at 30 concurrent calls means 120 dropped calls. Local fallback with degraded latency is better than no service.

### 5.3 LLM: Local vLLM (Primary) + Groq (Overflow)

**Primary: vLLM + local model**
- Server: vLLM (continuous batching, OpenAI-compatible API)
- Quantization: AWQ 4-bit for maximum concurrency headroom
- VRAM: ~5-8 GB per model instance
- Overflow: When queue depth > 15, route to Groq

**Model selection — TO BE VALIDATED IN PHASE 0:**

The plan does NOT commit to Qwen 2.5 7B. Phase 0 benchmarks three candidates:

| Model | Spanish Quality | Function Calling | Size (AWQ) | Notes |
|---|---|---|---|---|
| Qwen 2.5 7B-Instruct | Strong | Good | ~5 GB | Best multilingual benchmarks at 7B |
| Llama 3.1 8B-Instruct | Good | Good | ~5 GB | Largest community, most tooling |
| Gemma 2 9B-Instruct | Good | Moderate | ~6 GB | Higher quality per param but larger |

Benchmark criteria for voice agents (Phase 0):
- Natural conversational response in Spanish (not stilted, not overly formal)
- Function calling accuracy over 20 test scenarios (target: >90%)
- Hallucination rate on RAG-backed questions (target: <10%)
- First-token latency at 1, 10, and 20 concurrent requests
- Quality of responses at 4-bit quantization vs full precision

**Groq overflow:**
- Llama 3.1 8B at ~$0.003/min
- ~200ms first-token latency (fast cloud inference)
- Activates automatically when local vLLM queue depth exceeds threshold
- Estimated 5-15% of calls at peak hours → ~$15-45/month

### 5.4 TTS: Kokoro (Primary) — IF Spanish Validates in Phase 0

**Kokoro is the plan's biggest quality risk.** It's fast (50-150ms TTFB), tiny (0.5 GB VRAM), and free (Apache 2.0). But Spanish voice quality is community-contributed and unvalidated for phone conversations.

**Phase 0 validates with a structured test:**
1. Generate 20 Spanish sentences covering: greetings, questions, technical explanations, emotional responses
2. Play through G.711 codec (8kHz, simulating phone quality)
3. Rate: naturalness, intelligibility, accent appropriateness
4. Compare against Fish Speech and ElevenLabs at the same sentences

**If Kokoro passes:** Use it. $0/min, 50-150ms TTFB, tiny VRAM footprint.

**If Kokoro fails, fallback options (ranked by preference):**

| Option | TTFB | VRAM | Cost | Trade-off |
|---|---|---|---|---|
| Fish Speech 1.5 | 200-400ms | ~4-6 GB | $0 (Apache 2.0) | +150-250ms latency, +4 GB VRAM |
| F5-TTS | 300-600ms | ~4-6 GB | $0 (open) | +250-450ms latency, highest quality cloning |
| ElevenLabs API | 150-250ms | 0 | $0.015/min ($1,800/mo) | Cloud dependency, but excellent quality |

If we fall back to Fish Speech, the VRAM budget changes: vLLM (8 GB) + Fish Speech (5 GB) + faster-whisper fallback (4 GB) = 17 GB on a 24 GB card. Tight but fits. No room for a second LLM instance.

### 5.5 Telephony: Telnyx

| Feature | Telnyx Provides | Notes |
|---|---|---|
| Phone numbers | US, Canada, Mexico, 40+ countries | API provisioning |
| SIP termination | Yes (their core business) | No LiveKit SIP needed in Stage 1 |
| WebSocket audio | TeXML media streams | 8kHz mulaw, bidirectional |
| DTMF | WebSocket events | In-band and out-of-band |
| Call transfer | REST API | Cold and warm |
| Voicemail detection | API feature | Outbound calls |
| Call queuing | Built-in | Configurable |
| Cost | ~$0.007/min US | ~50% cheaper than Twilio |

**Telnyx vs Twilio for Mexico:** Telnyx has Mexico DIDs and good LATAM routing. Twilio has deeper Mexico infrastructure. If call quality issues arise with Telnyx Mexico numbers, Twilio is the fallback at ~$0.013/min.

### 5.6 Turn Detection

LiveKit's turn-detection model works standalone (it's a HuggingFace model, not tied to LiveKit infrastructure). Pipecat also has its own Smart Turn model.

**Plan:** Use LiveKit's turn detector in Phase 0 benchmarks alongside Pipecat's Smart Turn. Pick whichever performs better in Spanish.

| Model | Architecture | Latency | GPU Required | Spanish |
|---|---|---|---|---|
| LiveKit turn-detector | Transcript-based transformer (Qwen2.5-0.5B) | 50-160ms CPU | No | 14 languages including Spanish |
| Pipecat Smart Turn v2 | Audio-based wav2vec2 | 12-75ms GPU, 410ms+ CPU | Yes for low latency | 14 languages including Spanish |

LiveKit's model has an advantage: CPU-only, no GPU needed. Pipecat's model has an advantage: works on raw audio (captures prosody, filler words that STT misses). For phone audio where background noise is common, audio-based detection may be more reliable.

### 5.7 Knowledge Base: pgvector + Local Embeddings

| Component | Choice | Rationale |
|---|---|---|
| Vector store | pgvector (in PostgreSQL) | No separate service. Sufficient for <500K chunks. |
| Embedding model | `all-MiniLM-L6-v2` (local, free) | 384-dim, ~80MB, CPU inference in <50ms. Quality is ~90% of OpenAI embeddings. |
| Fallback embedding | OpenAI `text-embedding-3-small` | If local embedding quality is insufficient for specific domains |
| Chunking | 512 tokens, 50-token overlap | Standard for conversational retrieval |
| Retrieval | Top-3 chunks, similarity threshold 0.6 | Configurable per agent |

**Local embeddings preferred** over OpenAI to avoid another cloud dependency and per-token cost. `all-MiniLM-L6-v2` is fast enough for real-time retrieval and free.

---

## 6. Agent Model

### What an Agent Is

An agent is a configuration record stored in PostgreSQL, not a running process. When a call arrives, the system loads the agent config and initializes a Pipecat pipeline with those parameters.

```
Agent {
  id: uuid
  name: string
  phone_number: string                      # Telnyx number assigned
  system_prompt: string                     # Core personality and instructions
  language: "es" | "en" | "multi"
  voice: string                             # TTS voice ID
  voice_speed: float (0.5-2.0)
  llm_model: string                         # "local/qwen2.5-7b" or "groq/llama-3.1-8b"
  llm_temperature: float (0-1)
  tools: Tool[]                             # Functions the agent can call
  knowledge_base_id: uuid | null            # RAG document collection
  flow: ConversationFlow | null             # State machine definition (YAML/JSON)
  max_call_duration: int (seconds)          # Default: 600 (10 min)
  silence_timeout: int (seconds)            # End call after N seconds silence
  disclosure_message: string                # "Esta llamada está siendo grabada" — REQUIRED
  success_criteria: string | null           # For post-call analysis
  webhook_url: string | null                # Call lifecycle events
  dynamic_variables: Record<string, string> # Default variables
  precached_phrases: string[]               # Pre-generated TTS audio for common responses
  created_at: timestamp
}
```

### Conversation Flows as Data (Not Code)

v2 defined flows as Python code. This requires a deploy for every flow change and doesn't validate before deployment. Flows are now YAML/JSON documents stored in the agent config, interpreted at runtime by the flow engine.

```yaml
# Example: appointment booking flow
initial_state: greeting

states:
  greeting:
    prompt: "Greet the caller and ask how you can help"
    transitions:
      - condition: "caller wants appointment"
        target: collect_info
      - condition: "caller has question"
        target: answer_question

  collect_info:
    prompt: "Ask for their name, preferred date, and time"
    tools: [check_availability]
    transitions:
      - condition: "info collected and slot available"
        target: confirm
      - condition: "slot not available"
        target: suggest_alternative

  suggest_alternative:
    prompt: "Suggest the nearest available times"
    tools: [check_availability]
    transitions:
      - condition: "caller accepts alternative"
        target: confirm
      - condition: "caller wants different date"
        target: collect_info

  confirm:
    prompt: "Confirm the appointment details and book it"
    tools: [book_appointment]
    transitions:
      - condition: "confirmed"
        target: farewell
      - condition: "wants to change"
        target: collect_info

  farewell:
    prompt: "Thank them and end the call"
    end: true

  answer_question:
    prompt: "Answer using knowledge base"
    use_knowledge_base: true
    transitions:
      - condition: "question answered"
        target: farewell
      - condition: "needs appointment"
        target: collect_info
```

**Advantages over Python-defined flows:**
- Update via API without deploy: `PATCH /agents/{id}` with new flow YAML
- Validate flow graph before deployment (no orphan states, no missing transitions)
- Version via the database, not git (though git is fine too)
- Non-developer-writable (still text, but no Python knowledge needed)
- Visual builder can be added later — it would generate/consume this same YAML

### Spawning a New Agent

```
POST /agents
{
  "name": "Soporte Técnico MiEmpresa",
  "system_prompt": "Eres un agente de soporte técnico para MiEmpresa...",
  "language": "es",
  "voice": "kokoro_es_male_1",
  "disclosure_message": "Esta llamada está siendo grabada para fines de calidad.",
  "tools": [
    {
      "name": "check_ticket_status",
      "description": "Verificar el estado de un ticket de soporte",
      "endpoint": "https://api.miempresa.com/tickets/{ticket_id}",
      "method": "GET"
    },
    {
      "name": "create_ticket",
      "description": "Crear un nuevo ticket de soporte",
      "endpoint": "https://api.miempresa.com/tickets",
      "method": "POST",
      "parameters": {
        "subject": "string",
        "description": "string",
        "priority": "low|medium|high"
      }
    }
  ],
  "knowledge_base": {
    "documents": ["https://miempresa.com/faq", "/path/to/manual.pdf"],
    "auto_refresh": true
  },
  "precached_phrases": [
    "Un momento por favor, estoy verificando.",
    "¿Me puede repetir su nombre?",
    "Gracias por su paciencia."
  ]
}
→ Returns: agent ID + assigned phone number. Agent is live.
```

---

## 7. Failure Modes

The previous plan had zero failure analysis. This section defines what happens when things break.

### Deepgram Goes Down

**Detection:** STT response latency >1 second or WebSocket disconnect.
**Action:** Activate local faster-whisper (distil-large-v3, already loaded in VRAM).
**Impact:** Latency increases by ~500-1000ms. Callers experience slower responses but calls don't drop.
**Recovery:** Monitor Deepgram health. Switch back automatically when latency returns to normal.

### GPU Server Crashes

**Detection:** vLLM and Kokoro health checks fail.
**Action — immediate:** Route all new calls to Groq API (LLM) + ElevenLabs API (TTS) as emergency cloud fallback. Cost increases to ~$0.05/min for affected calls.
**Action — active calls:** Calls in progress lose LLM/TTS. Agent says pre-recorded "We're experiencing technical difficulties, please call back" and disconnects gracefully.
**Prevention:** Systemd watchdog for vLLM and Kokoro processes. Auto-restart on crash. For true HA, second GPU server (adds ~$800/month).

### LLM Overloaded (Queue Depth Too High)

**Detection:** vLLM reports queue depth > 15 pending requests.
**Action:** New calls route to Groq API. Existing calls continue on local vLLM.
**Impact:** Groq calls cost ~$0.003/min extra. Latency may actually improve (Groq is fast).
**Recovery:** Automatic. When queue depth drops below 10, resume local routing.

### Agent Gives Wrong Answer

**Detection:** Post-call analysis identifies low confidence or caller frustration.
**Mitigation layers:**
1. RAG retrieval reduces hallucination by grounding responses in documents
2. System prompt includes guardrails ("If you don't know the answer, say so and offer to transfer to a human")
3. Function calling for `transfer_to_human` is always available as an escape hatch
4. Post-call analysis flags calls with negative sentiment for human review

### Telnyx Outage

**Detection:** No incoming calls or WebSocket failures.
**Action:** Manual switchover to Twilio (pre-configured as backup trunk). Not automated — Telnyx outages are rare, and automatic failover between telephony providers is complex.
**Prevention:** Monitor Telnyx status page. Keep Twilio account funded with backup numbers.

### Call Disclosure (Legal)

Every agent MUST have a `disclosure_message` field. This message plays at the start of every call before the agent speaks. Example: "Esta llamada está siendo grabada para fines de calidad y entrenamiento."

This is not optional. Mexican telecommunications law (Ley Federal de Telecomunicaciones, LFPDPPP) requires consent notification for call recording. The system enforces this — agents without a disclosure message cannot be activated.

---

## 8. Retell Feature Coverage

### Building (Critical Path)

| Feature | Phase | Notes |
|---|---|---|
| Single-prompt agents | 1 | System prompt → agent. Simplest path. |
| Inbound call handling | 1 | Phone number → agent routing |
| Call recording + transcription | 1 | MinIO + PostgreSQL |
| Recording disclosure | 1 | Mandatory pre-call message |
| STT fallback (local) | 1 | faster-whisper activates on Deepgram failure |
| Outbound calls via API | 2 | `POST /calls` |
| Function calling (sync + async) | 2 | Per-agent HTTP tools |
| Dynamic variables | 2 | `{{variable}}` in prompts |
| Call transfer (cold) | 2 | Via Telnyx REST API |
| DTMF handling | 2 | Telnyx WebSocket events |
| Webhooks | 2 | call_started, call_ended, call_analyzed |
| Knowledge base (RAG) | 3 | pgvector, local embeddings |
| Conversation flows (YAML) | 4 | State machine with LLM-evaluated transitions |
| Warm call transfer | 4 | Two-leg call, agent provides context |
| Sentence-level TTS streaming | 4 | LLM → sentence detect → Kokoro |
| Pre-cached responses | 4 | TTS pre-generated for common phrases |
| Post-call analysis | 5 | LLM summary, sentiment, success, extractors |
| Latency metrics per call | 5 | VAD, STT, LLM, TTS timestamps |
| Monitoring (Prometheus + Grafana) | 5 | Call volume, latency, errors |
| LLM overflow to Groq | 6 | Automatic on queue depth threshold |
| Batch outbound calling | 6 | CSV dispatch |
| Health checks + auto-recovery | 6 | Watchdog for all services |

### Building Later (When Needed)

| Feature | Trigger |
|---|---|
| LiveKit migration | >20 consistent concurrent calls |
| Web playground (browser test) | When phone-minute cost for testing becomes a pain |
| Voicemail detection | When outbound calling volume justifies it |
| Silence reminders | Phase 4 stretch goal |
| Backchannel ("uh-huh") | Phase 4 stretch goal |
| Boosted keywords for Deepgram | When domain-specific terms cause STT errors |
| Voice cloning | When clients request branded voices |
| GPU HA (second server) | When uptime SLA matters |

### Not Building

| Retell Feature | Reason |
|---|---|
| Visual flow builder | YAML flows are sufficient. Add UI only if non-developers need to create flows. |
| React dashboard | API + Grafana. Admin via CLI/API. |
| Multi-tenant / RBAC | Single operator. Add only if reselling. |
| Chat widget / SMS / omnichannel | Voice-first. |
| Simulation testing harness | Test with real calls + post-call analysis. |
| QA cohorts with AI scoring | Post-call analysis is sufficient. |
| Ambient sounds | Trivial and zero priority. |
| A/B testing | Compare call analysis results manually. |
| PII scrubbing | Build when handling regulated data. |

---

## 9. Build Phases

### Phase 0 — Validate Assumptions (1 week)

**Goal:** Kill the project's biggest risks before writing any infrastructure code.

This phase is pure benchmarking. No infrastructure, no API, no pipeline. Just model evaluation scripts.

**LLM benchmark:**
1. Run Qwen 2.5 7B, Llama 3.1 8B, and Gemma 2 9B through 50 Spanish conversational prompts
2. Test function calling accuracy across 20 scenarios (booking, ticket creation, status checks)
3. Measure first-token latency at 1, 10, and 20 concurrent requests via vLLM
4. Evaluate 4-bit AWQ quality vs full precision — is the degradation acceptable?
5. Test RAG-grounded responses: 20 questions with provided context chunks, measure hallucination rate

**TTS benchmark:**
6. Generate 20 Spanish sentences with Kokoro, Fish Speech, and F5-TTS
7. Downsample to 8kHz G.711 (phone codec simulation)
8. Evaluate: naturalness, intelligibility, accent (Mexico-appropriate?)
9. Measure TTFB at 1 and 10 concurrent requests

**Turn detection benchmark:**
10. Record 20 Spanish conversation fragments with various pause patterns
11. Test LiveKit turn-detector and Pipecat Smart Turn on detection accuracy
12. Measure false-positive rate (premature turn end) and false-negative rate (too slow)

**Exit criteria:** Clear winner for LLM model, TTS engine, and turn detector. If no model passes quality bar for Spanish, reassess project scope. Document results in `docs/phase0-benchmarks.md`.

---

### Phase 1 — First Phone Call (2-3 weeks)

**Goal:** Call a number, AI answers with recording disclosure, have a conversation in Spanish, hang up. Transcript and recording stored.

**Infrastructure:**
1. Docker Compose: PostgreSQL + MinIO
2. GPU setup: vLLM (winning LLM from Phase 0) + Kokoro/Fish TTS + faster-whisper fallback
3. Telnyx account: SIP trunk + first phone number

**Pipeline:**
4. Pipecat app with Telnyx WebSocket serializer
5. Deepgram STT plugin (streaming) + local faster-whisper fallback on Deepgram failure
6. LLM via OpenAI-compatible plugin → local vLLM
7. TTS plugin (winning engine from Phase 0), streaming
8. Silero VAD + winning turn detector from Phase 0
9. Recording disclosure: pre-recorded audio plays before agent starts

**API + storage:**
10. FastAPI: `POST /agents`, `GET /agents`, `GET /calls`
11. PostgreSQL schema: agents, calls, transcripts
12. Call recording: audio → MinIO, transcript → PostgreSQL

**Exit criteria:** Dial a phone number, hear disclosure message, have a 3-minute conversation in Spanish, verify transcript and recording are stored correctly.

---

### Phase 2 — Multi-Agent + Tools (2-3 weeks)

**Goal:** Multiple agents on different numbers, each with their own personality and tools.

**Agent configuration:**
1. Full agent model stored in PostgreSQL
2. Phone number → agent routing (Telnyx webhook → lookup agent → initialize pipeline)
3. Dynamic variables: `{{name}}`, `{{company}}` substitution in prompts

**Function calling:**
4. Tool definition per agent (name, description, parameters, HTTP endpoint, method)
5. Sync execution: agent waits for result, incorporates into response
6. Async execution: agent speaks filler ("Un momento, estoy verificando") while tool runs
7. HMAC signature on outgoing webhook requests
8. Built-in tools: end_call, transfer_call

**Telephony:**
9. Outbound call API: `POST /calls`
10. Cold call transfer via Telnyx REST API
11. DTMF detection via WebSocket events

**Webhooks:**
12. `call_started`, `call_ended` events to agent's `webhook_url`
13. Payload: call_id, agent_id, from/to numbers, duration, transcript

**Exit criteria:** 3 agents on 3 numbers. Agent A books appointments via HTTP tool. Agent B checks ticket status. Agent C answers questions. All handle tool calls correctly.

---

### Phase 3 — Knowledge Base (2 weeks)

**Goal:** Agents answer questions from uploaded documents accurately.

**Document pipeline:**
1. Upload API: PDF, DOCX, TXT, MD, CSV, HTML
2. Text extraction → chunking (512 tokens, 50 overlap) → embedding (local `all-MiniLM-L6-v2`) → pgvector
3. Per-agent KB assignment via foreign key

**Retrieval during calls:**
4. On each user turn: embed utterance → cosine similarity → inject top-3 chunks into LLM context
5. Retrieval latency target: <50ms (pgvector with HNSW index)
6. Configurable per agent: chunk count, similarity threshold

**Management:**
7. URL sources: fetch and index web pages
8. Auto-refresh: re-crawl URLs every 24h (background worker)
9. KB status API: indexing progress, document count, chunk count

**Exit criteria:** Upload a 20-page product manual in Spanish. Call the agent. Ask 10 questions from the manual. Agent answers 8+ correctly with specific details.

---

### Phase 4 — Latency Optimization + Conversation Flows (4-6 weeks)

**Goal:** p50 latency <1,000ms. Structured multi-step conversations.

This is the hardest phase. Budget extra time.

**Sentence-level streaming (week 1-2):**
1. Detect sentence boundaries in LLM output stream (`.`, `?`, `!`, `\n`)
2. Send each sentence to TTS immediately while LLM continues generating
3. Audio chunks stream to caller as they're produced
4. Handle edge case: interruption during streaming (cancel remaining TTS, process new user input)

**Pre-cached responses (week 2):**
5. At agent creation: generate TTS for `precached_phrases`
6. Pattern matching: if LLM output matches a cached phrase, play cached audio instantly (0ms TTS)
7. Cache invalidation: regenerate when agent voice settings change

**Turn-taking refinement (week 2-3):**
8. Per-agent interruption sensitivity (configurable)
9. Block interruptions during critical speech (tool result delivery, disclosure)
10. Silence reminders: "¿Sigue ahí?" after configurable timeout

**Conversation flows (week 3-5):**
11. Flow engine: interprets YAML flow definitions at runtime
12. State machine: current state, variables, transition evaluation
13. Transition types: equation conditions (`variable == value`) evaluated first, then LLM prompt conditions
14. Per-state prompt injection: state's prompt augments the global system prompt
15. Variable extraction: LLM extracts named values from conversation into flow variables
16. Warm call transfer: create second call leg, agent provides context, bridge

**Latency instrumentation (week 5-6):**
17. Timestamp every pipeline stage per turn: VAD_end, STT_final, LLM_first_token, LLM_first_sentence, TTS_first_byte, audio_play
18. Calculate and store: e2e, STT, LLM, TTS latencies
19. API: `GET /calls/{id}/latency` with per-turn breakdown
20. Aggregation: p50/p90/p95/p99 per agent

**Exit criteria:** p50 latency <1,000ms over 100 test calls (mixed Spanish/English). A 5-state appointment booking flow completes successfully. Latency breakdown visible per call.

---

### Phase 5 — Call Analysis + Monitoring (2 weeks)

**Goal:** Understand agent performance. Catch failures. Dashboards.

**Post-call analysis:**
1. On `call_ended`: send transcript to LLM (can use cheaper model, e.g., local Qwen or Groq)
2. Extract: summary, user sentiment, success/failure (per agent's `success_criteria`)
3. Custom extractors: per-agent fields (e.g., `ticket_number: string`, `issue_resolved: boolean`)
4. Store in PostgreSQL. Fire `call_analyzed` webhook.

**Monitoring:**
5. Prometheus metrics exported from the Python app:
   - `pipesong_calls_total{agent, status}` — counter
   - `pipesong_calls_active{agent}` — gauge
   - `pipesong_call_duration_seconds{agent}` — histogram
   - `pipesong_latency_e2e_ms{agent}` — histogram
   - `pipesong_latency_stt_ms`, `_llm_ms`, `_tts_ms` — histograms
   - `pipesong_errors_total{agent, component}` — counter
   - `pipesong_deepgram_fallback_active` — gauge
   - `pipesong_llm_overflow_active` — gauge
6. Grafana dashboards: call volume, latency trends, success rate, per-agent breakdown, fallback activation
7. Alerting: latency p95 > 1,500ms, error rate > 5%, Deepgram fallback active > 5min

**API:**
8. `GET /calls` with filters (agent, date range, success, sentiment)
9. `GET /calls/{id}` with full transcript, analysis, latency, recording URL
10. `GET /agents/{id}/stats` — aggregated metrics

**Exit criteria:** Grafana shows real-time metrics. Post-call analysis correctly classifies success/failure on 90%+ of test calls.

---

### Phase 6 — Scale + Production Hardening (3-4 weeks)

**Goal:** Handle 30-50 concurrent calls reliably. Outbound batch calling. Automatic overflow.

**LLM overflow:**
1. Monitor vLLM `/health` and queue depth
2. When queue depth > threshold: route new calls to Groq API automatically
3. Log overflow events for capacity planning
4. Dashboard panel: overflow rate over time

**Batch calling:**
5. `POST /batch-calls` with CSV (phone numbers + per-row dynamic variables)
6. Concurrency control: configurable max simultaneous outbound calls
7. Rate limiting: respect Telnyx CPS limits
8. Status tracking per row: pending → dialing → in_progress → completed/failed
9. Voicemail detection: analyze initial audio, configurable action (hangup/leave message)

**Reliability:**
10. Health checks: vLLM, Kokoro, Deepgram connectivity, PostgreSQL
11. Auto-restart on crash (Docker restart policy + systemd watchdog)
12. Graceful shutdown: stop accepting new calls, finish active calls, then exit
13. Connection retry: Deepgram WebSocket auto-reconnect, vLLM request retry with backoff

**Evaluate LiveKit migration:**
14. If concurrent calls consistently >20: benchmark Pipecat + Telnyx vs LiveKit at 30-50 concurrent
15. If LiveKit wins: migrate transport layer (pipeline code stays the same)
16. Document decision in `docs/livekit-evaluation.md`

**Load testing:**
17. Simulate concurrent calls (use Telnyx test numbers or internal SIP client)
18. Measure: latency at 10/20/30/40/50 concurrent, error rate, overflow activation
19. Document scaling thresholds: "add second GPU at X concurrent calls"

**Exit criteria:** 30 concurrent calls sustained for 30 minutes with p95 < 1,500ms. LLM overflow activates and deactivates cleanly. Batch call of 100 numbers completes.

---

## 10. Timeline (Honest)

| Phase | Work | Estimate | Cumulative |
|---|---|---|---|
| 0 — Benchmarks | Model evaluation, no infrastructure | 1 week | 1 week |
| 1 — First call | Pipeline + Telnyx + basic API | 2-3 weeks | 3-4 weeks |
| 2 — Multi-agent + tools | Agent config, routing, function calling | 2-3 weeks | 5-7 weeks |
| 3 — Knowledge base | RAG pipeline, pgvector, retrieval | 2 weeks | 7-9 weeks |
| 4 — Latency + flows | Sentence streaming, caching, flow engine | 4-6 weeks | 11-15 weeks |
| 5 — Analysis + monitoring | Post-call analysis, Prometheus, Grafana | 2 weeks | 13-17 weeks |
| 6 — Scale + hardening | Overflow, batch calling, load testing | 3-4 weeks | 16-21 weeks |

**Total: 16-21 weeks.** Not the 12 weeks from v2. Phase 4 alone is 4-6 weeks because the sentence-streaming pipeline and flow engine are the most technically challenging work.

**Parallelizable:** Phase 5 (monitoring) can start during Phase 4. Phase 0 (benchmarks) is prerequisite for everything — don't skip it.

---

## 11. Technical Risks (Revised)

| Risk | Probability | Impact | Mitigation | Worst Case |
|---|---|---|---|---|
| **Kokoro fails Spanish** | Medium | High — need different TTS | Phase 0 validates. Fish Speech is ready backup. | +150-250ms latency, +4 GB VRAM |
| **7B LLM too shallow** | Medium | High — need cloud LLM | Phase 0 validates. Groq overflow covers it. | Cost increases to ~$0.03-0.04/min |
| **Deepgram outage** | Low | High — all calls affected | Local faster-whisper fallback loaded and ready | +500-1000ms latency during outage |
| **LLM latency at concurrency** | High | Medium — p95 blows budget | Groq overflow at queue depth threshold | 5-15% of peak calls go to Groq |
| **Overlap pipeline harder than expected** | High | Medium — latency stays >1.2s | Fall back to sequential + first-word-priority trick | p50 stays at 1.1-1.3s instead of <1.0s |
| **GPU server failure** | Low | Critical — all calls drop | Groq + ElevenLabs emergency cloud fallback | ~$0.05/min, degraded but functional |
| **Telnyx Mexico quality** | Medium | Medium — poor call quality | Switch to Twilio for Mexico numbers | +$0.006/min |
| **Interim transcript speculation fails** | Already cut | N/A | Removed from plan in v3 | N/A |
| **Turn detection poor in Spanish** | Medium | Medium — bad UX | Phase 0 benchmarks both models. Tune VAD params. | Stick with conservative VAD timing |

---

## 12. Success Metrics

| Milestone | Definition | Phase |
|---|---|---|
| **Models validated** | LLM, TTS, and turn detector pass Spanish quality benchmarks | 0 |
| **First call** | AI answers phone, converses in Spanish, stores transcript | 1 |
| **Multi-agent** | 5+ agents, each topic-trained with KB, handling calls | 3 |
| **Optimized** | p50 latency <1,000ms over 100 test calls | 4 |
| **Observable** | Grafana dashboards, post-call analysis, per-call latency | 5 |
| **Production** | 30 concurrent calls, overflow working, batch calls complete | 6 |
| **Cost target** | Operating at <$0.03/min all-in | 6 |
