# Pipesong — Technical Plan

## 1. Project Goal

Build an open-source, self-hostable alternative to [Retell AI](https://docs.retellai.com) — a complete platform for building, testing, deploying, and monitoring AI phone agents.

**Differentiation from existing OSS projects:**
- **Pipecat** — pipeline framework only (no UI, no telephony management, no SaaS layer)
- **LiveKit Agents** — agent framework only (no UI, no agent management, no analytics)
- **Vocode** — stalled development, simpler architecture
- **Bolna** — batteries-included but less modular
- **Dograh** — low-code builder, very early stage

Pipesong is the **platform layer** on top of the framework layer. Not competing with Pipecat/LiveKit — building on them.

---

## 2. Retell AI Feature Audit

Full feature analysis of what Retell AI provides, to define our build scope.

### 2.1 Agent Types

**Single Prompt Agent**
- One comprehensive system prompt defines all behavior
- Best for simple conversations, quick prototypes, 1-3 functions
- Degrades above ~1000 words or 5+ functions (behavioral drift, unreliable tool calls)

**Multi Prompt Agent**
- Structured tree of states, each with its own prompt, tools, and transition logic
- Variables flow between states preserving context
- Scales better than single-prompt for complex scenarios

**Conversation Flow Agent**
- Visual node-and-edge graph with 11 node types
- Most deterministic: fine-grained control over conversation pathways
- Per-node LLM/voice selection enables cost optimization
- Retell's most powerful and complex agent type

### 2.2 Conversation Flow — Node Types (11)

| Node | Purpose |
|---|---|
| **Conversation** | Primary dialogue. Supports prompt or static text, multi-turn within node, per-node LLM, block interruptions, attached functions |
| **Function** | Executes tool on entry. Optional wait-for-result (sync/async). Transitions based on result |
| **Logic Split** | Conditional branching. Equation-based conditions on variables. Instant, no speech |
| **End** | Terminates call. Optional farewell message |
| **Call Transfer** | Cold, warm (non-agentic), or agentic warm transfer. Configurable timeout, caller ID, hold music, whisper messages |
| **Transfer Agent** | Switch to another AI agent mid-call. New agent gets full history |
| **Extract Variable** | LLM extracts text/number/enum/boolean from conversation into a variable |
| **Press Digit** | Navigate IVR menus by pressing DTMF tones |
| **SMS** | Send one-way SMS mid-call |
| **MCP** | Call tools on external MCP servers |
| **Global** | Universal handler triggered from any point in the flow based on activation condition |

**Transition conditions (2 types):**
- **Equation conditions** — hardcoded logic: `==`, `!=`, `>`, `<`, `AND`, `OR`, `CONTAINS`, `NOT CONTAINS`, `exists`, `does not exist`. Evaluated first, top-to-bottom.
- **Prompt conditions** — natural language evaluated by LLM (e.g., "User confirmed the appointment"). Evaluated after all equation conditions.

**Components** — reusable sub-flows, either library-scoped (account-wide) or agent-scoped.

**Flex Mode** — compiles all node instructions into a single prompt at runtime. More flexible navigation but significantly higher LLM costs.

### 2.3 Voice & Turn-Taking

| Feature | Detail |
|---|---|
| TTS providers | Retell platform, ElevenLabs, Cartesia, MiniMax, OpenAI, Fish, Deepgram |
| Voice cloning | Upload recordings → custom voice. Max 100 per account |
| Voice parameters | Temperature (0-2), speed (0.5-2), volume (0-2), emotion (7 options), dynamic speed |
| TTS fallback | Automatic fallback chain across providers. Gender-matched. Persists for rest of call |
| Responsiveness | 0-1 slider controlling response speed. Dynamic responsiveness available |
| Interruption sensitivity | 0-1 slider. Per-node block interruptions |
| Backchannel | Optional "uh-huh" / "mm-hmm" with configurable frequency. 8 languages |
| Silence reminders | Configurable trigger (default 10s), max count, auto-end threshold |
| Endpointing | Fast/accurate/custom modes. Configurable `endpointing_ms` per STT provider |
| Background noise | No denoise, noise cancellation (free), noise + speech cancellation (+$0.005/min) |
| Ambient sounds | Coffee shop, convention hall, outdoor, static, call center |
| Boosted keywords | Array of domain terms for better ASR recognition |
| Pronunciation dictionary | Custom IPA/CMU phonetics |

**Latency claims:**
- Headline: ~600ms
- LLM normal range: 500-900ms
- KB retrieval: <100ms
- Per-call latency breakdown: e2e, ASR, LLM, TTS, KB (p50, p90, p95, p99, min, max)

### 2.4 Function Calling

**10 pre-built functions:** End Call, Transfer Call, Transfer Agent, Press Digit, Check Calendar (Cal.com), Book Appointment (Cal.com), Send SMS, Extract Variable, MCP Tool, Custom Function.

**Custom function protocol:**
- LLM decides when to call based on conversation context
- HTTP request to your endpoint (GET/POST/PATCH/PUT/DELETE)
- Request body: `{ name, args, call }` (call contains real-time transcript, metadata, variables)
- HMAC signature verification via `X-Retell-Signature` header
- Response capped at 15,000 characters
- Configurable timeout (default 2 min), up to 2 retries
- "Speak during execution" — agent fills silence while function runs
- "Speak after execution" — LLM generates response about the result

### 2.5 Knowledge Base / RAG

- 26 document formats supported
- Sources: URLs (up to 500 per KB), files (25, 50MB each), text snippets (50), CSV/Excel
- Auto-sync: re-fetch URLs every 24h, auto-crawl all pages under each path
- Retrieval: embed transcript → cosine similarity → top-K chunks injected into LLM context
- Configurable: chunks to retrieve (1-10, default 3), similarity threshold (default 0.60)
- Agent-level and per-node knowledge bases
- Latency: <100ms per retrieval
- Pricing: first 10 KBs free, $8/month each after. +$0.005/min during calls

### 2.6 Telephony

| Feature | Detail |
|---|---|
| Phone numbers | US/Canada. $2/month standard, $5/month toll-free |
| Inbound | Bind agent to number. Inbound webhook for dynamic agent selection (10s timeout, 3 retries) |
| Outbound | REST API. Default 1 call/sec. Twilio max 5 CPS, Telnyx max 16 CPS |
| Batch calling | CSV upload. Schedule or immediate. +$0.005/dial |
| International | 16 countries (Twilio), 3 countries (Telnyx). $0.015-$0.80/min |
| Custom SIP | Elastic SIP trunking (SIP endpoint + IP whitelist) or dial-to-SIP-URI |
| Call transfer | Cold (SIP REFER), warm non-agentic, agentic warm (separate agent handles transfer) |
| IVR navigation | Press digit node/function. Configurable detection delay |
| DTMF input | Automatic capture. Completion via digit limit, termination key, or timeout |
| Voicemail detection | Continuous for first 3 min of outbound. Actions: hangup, static text, prompt, bridge |
| Branded caller ID | +$0.10/outbound call |
| Verified numbers | $10/month |

### 2.7 Testing

- **Playground** — interactive web-based testing
- **Simulation** — LLM plays user with configurable identity (name, DOB), goal, personality
- **Batch simulation** — multiple test cases simultaneously
- **Function mocking** — prevent actual function calls for deterministic testing
- **Test modes** — web (browser WebRTC) or phone (actual call)

### 2.8 QA (Quality Assurance)

- **Cohorts** — named groups of calls filtered by agent, time, duration, analysis results
- **Sampling** — configurable % inclusion, weekly caps
- **AI-evaluated conditions** — custom natural-language criteria assessed against transcript
- **Performance metrics** — latency, sentiment, interruptions, transcription WER, entity errors, hallucination, tool call accuracy, node transition accuracy, naturalness
- **Pricing** — first 100 min/workspace free, $0.10/min after

### 2.9 Webhooks & Events

| Event | Trigger |
|---|---|
| `call_started` | New call begins |
| `call_ended` | Call completes/transfers/errors |
| `call_analyzed` | Post-call analysis done |
| `transcript_updated` | Turn-taking updates + final |
| `transfer_started/bridged/cancelled/ended` | Transfer lifecycle |
| `chat_started/ended/analyzed` | Chat events |

- HTTP POST, JSON. 10s timeout, 3 retries. HMAC verification.
- Inbound call webhook for dynamic agent selection and variable injection.

### 2.10 Call Analysis

- **Built-in:** summary, sentiment, success/failure, in_voicemail
- **Custom extractors:** boolean, text, number, selector (fixed-list categorization)
- **Configurable prompts:** success criteria, summary guidance, sentiment guidance (max 2000 chars each)
- **Post-call analysis model** selectable (default: gpt-4.1-mini)
- **Rerun analysis** via API/dashboard

### 2.11 API Surface

~50 REST endpoints across: Agents, Chat Agents, Calls, Chats, Phone Numbers, Conversation Flows, Flow Components, Knowledge Bases, Voices, Batch Calls, Test Cases, Test Runs.

**LLM WebSocket Protocol** for custom LLM integration: 5 inbound message types (ping, call_details, update_only, response_required, reminder_required), 7 outbound types (config, ping, response, agent_interrupt, tool_call_invocation, tool_call_result, update_agent).

**SDKs:** JavaScript/TypeScript, Python, Web client (WebRTC).

### 2.12 Omnichannel

- **Voice agents** — phone calls (inbound/outbound) + web calls (WebRTC)
- **Chat agents** — separate type from voice agents. Deploy via widget, completion API, or SMS
- **Chat widget** — single `<script>` tag. Customizable theme, reCAPTCHA protection
- **SMS** — one-way during calls (SMS node/function), two-way conversations (chat agent on same number)
- Voice and chat agents are **separate objects** sharing the same flow/prompt framework

### 2.13 Enterprise

- **Compliance:** SOC2 Type II, HIPAA (BAA available), GDPR (DPA available), PCI-DSS, ISO 27001
- **RBAC:** Admin (full), Developer (build/test, no billing), Member (read-only)
- **SSO:** Enterprise plan
- **Privacy:** Storage levels (everything / everything_except_pii / basic_attributes_only), PII scrubbing (13 categories), data retention (1-730 days), signed secure URLs (24h TTL)
- **Guardrails:** Output moderation (9 categories), input jailbreak detection. ~50ms latency, +$0.005/min
- **Deployment:** Cloud, VPC, on-premises, air-gapped
- **Reliability:** 99.99% uptime (enterprise), TTS/LLM fallback+retry, outage mode (fallback numbers)
- **Fraud protection:** IP + phone rate limiting, geographic restrictions, KYC verification
- **Other:** A/B testing, agent versioning (JSON diff), concurrency management (20 free, $8/month per slot), analytics dashboards with custom charts, alerting (9 metrics)

### 2.14 Pricing Model

Per-minute additive: infrastructure ($0.055) + TTS ($0.015-0.040) + telephony ($0.015) + LLM ($0.003-0.080). Typical total: $0.07-0.31/min.

Add-ons: KB (+$0.005/min), denoising (+$0.005/min), guardrails (+$0.005/min), PII removal (+$0.01/min), batch calling (+$0.005/dial), branded caller ID (+$0.10/call).

---

## 3. Technology Decisions

### 3.1 Agent Framework: LiveKit Agents (not Pipecat)

| Dimension | LiveKit Agents | Pipecat |
|---|---|---|
| SIP/telephony | First-party SIP service, dispatch rules, trunk management | Delegates to Twilio/Telnyx WebSocket (no direct SIP) |
| Job orchestration | Built-in (agent server registers, jobs auto-dispatch) | None built-in |
| Scaling | Redis clustering, multi-region, graceful drain, K8s native | No built-in scaling |
| Turn detection | Transformer on CPU (50-160ms), open weights | wav2vec2 needs GPU (12-75ms GPU, 410ms+ CPU) |
| SDK languages | Python + Node.js | Python only |
| Self-hosting | Single Go binary + Redis. Full feature parity with cloud | Needs external transport (Daily or LiveKit) |

**Decision:** LiveKit Agents as primary framework. Pipecat remains an option via `LiveKitTransport` if we need its pipeline flexibility later.

### 3.2 STT: Deepgram (production) / faster-whisper (self-hosted)

The critical insight: **Whisper-based STT is not truly streaming.** It processes 1-3 second audio chunks in batch. This adds 1-2 seconds of latency compared to Deepgram's native streaming at 150-300ms.

| Solution | First-Token Latency | WER (English) | Cost |
|---|---|---|---|
| Deepgram Nova-3 | 150-300ms | ~8-12% | $0.0043/min |
| faster-whisper (large-v3, GPU) | 1-2s (chunked) | ~8-10% | GPU cost only |
| whisper.cpp (CPU) | 2-5s (windowed) | ~8-10% | CPU cost only |
| Ultravox (audio-native LLM) | 500ms-1s | ~10-15% | GPU cost only |

**Decision:** Deepgram for production (the latency difference is too large to ignore at $2.60/1000min). faster-whisper as self-hosted fallback. Ultravox as future experimental path to collapse STT+LLM.

### 3.3 TTS: Kokoro (primary) / XTTS-v2 (voice cloning)

| Model | TTFB (GPU) | Quality (MOS) | Voice Cloning | VRAM |
|---|---|---|---|---|
| Kokoro | 50-150ms | 3.8-4.2 | No (styles only) | ~0.5 GB |
| XTTS-v2 (idiap fork) | 200-500ms | 3.8-4.2 | Yes (6-15s ref) | ~4-6 GB |
| Fish Speech | 200-400ms | 3.8-4.2 | Yes (~10s ref) | ~4-6 GB |
| F5-TTS | 300-600ms | 4.0-4.3 | Yes (5-15s ref) | ~4-6 GB |
| StyleTTS2 | 300-800ms | 4.0-4.3 | Yes (adaptation) | ~3-5 GB |

**Decision:** Kokoro as default (fastest TTFB, smallest footprint, Apache 2.0). XTTS-v2 for voice cloning use cases. Architecture must be pluggable — users should be able to swap in ElevenLabs, Cartesia, etc.

### 3.4 Telephony: LiveKit SIP + Telnyx (no PBX needed)

Three tiers of telephony complexity:

| Tier | Architecture | When |
|---|---|---|
| 1. Simplest | Twilio/Telnyx WebSocket + Pipecat serializer | Fastest MVP path |
| 2. Self-hosted SIP | LiveKit SIP service + any SIP trunk | Default for Pipesong |
| 3. Full PBX | FreeSWITCH/Asterisk + mod_audio_fork/AudioSocket | Enterprise, 1000s concurrent calls |

**Decision:** Tier 2 (LiveKit SIP) as default. LiveKit SIP handles SIP termination, trunk management, dispatch rules, DTMF, and cold transfers without a PBX. Telnyx as recommended trunk provider (~50% cheaper than Twilio).

### 3.5 LLM: Provider-Agnostic (OpenAI-Compatible API)

Any OpenAI-compatible endpoint works: OpenAI, Anthropic (via proxy), Groq, Together AI, Ollama (local). The agent configuration stores `base_url` + `model` + `api_key`.

### 3.6 RAG: pgvector (no separate vector DB)

pgvector in PostgreSQL is sufficient for knowledge base retrieval at our scale. No need for a separate ChromaDB/Pinecone/Qdrant deployment. Simplifies infrastructure.

### 3.7 Turn Detection: LiveKit's Transformer Model

LiveKit's turn detector (Qwen2.5-0.5B fine-tuned for end-of-utterance prediction) runs on CPU at 50-160ms, supports 14 languages, and reduces false interruptions by ~85% vs. VAD alone. Open weights on Hugging Face.

### 3.8 Latency Budget

Breakdown of where time goes in the pipeline:

```
Stage                          | Optimistic  | Typical     | Pessimistic
-------------------------------|-------------|-------------|-------------
Audio capture + network        |    20ms     |    50ms     |   100ms
VAD (speech start detection)   |   200ms     |   300ms     |   500ms
VAD (speech end detection)     |   300ms     |   500ms     |  1000ms
STT processing                 |   200ms*    |   500ms*    |  2000ms
LLM first token                |   200ms     |   500ms     |  1500ms
TTS first audio byte           |   100ms     |   200ms     |   500ms
Audio playback start           |    20ms     |    50ms     |   100ms
-------------------------------|-------------|-------------|-------------
TOTAL                          |  1040ms     |  2100ms     |  5700ms

* Deepgram: 150-300ms. faster-whisper: 1000-2000ms.
```

**Optimization strategies:**
1. Sentence-level streaming — stream LLM output to TTS sentence-by-sentence (overlaps LLM + TTS)
2. VAD tuning — reduce endpoint silence from 500ms to 250ms (with continuation detection)
3. Speculative STT — start processing before VAD declares speech-end
4. Pre-warm all models — keep loaded in VRAM, zero cold-start

---

## 4. Phased Build Plan

### Phase 1 — Voice Pipeline MVP
**Goal:** Call a phone number, AI answers, have a conversation, hang up.
**Estimate:** 2-3 weeks

**Deliverables:**
1. Docker Compose: LiveKit Server + SIP service + Redis + PostgreSQL + MinIO
2. Telnyx SIP trunk setup + phone number
3. LiveKit Agent worker: `AgentSession` with STT -> LLM -> TTS
4. Deepgram STT (streaming) + Ollama LLM + Kokoro TTS
5. FastAPI server: create agent (name + system prompt), trigger outbound call
6. Call lifecycle: answer -> converse -> end on tool call or silence timeout
7. Call recording stored to MinIO
8. PostgreSQL schema: agents, calls, transcripts

**Not building:** UI, conversation flows, function calling, analytics, multi-tenant.

**Success criteria:** Make a phone call, have a coherent 2-minute conversation, see the transcript in the database.

---

### Phase 2 — Agent Configuration & Function Calling
**Goal:** Configurable agents with tools that take actions mid-call.
**Estimate:** 2-3 weeks

**Deliverables:**
1. Agent model: system prompt, voice, LLM provider/model, temperature, language
2. Voice parameters: speed, temperature, volume
3. Function calling framework:
   - Define tools per agent (name, description, parameters, HTTP endpoint)
   - Sync execution (wait-for-result) and async (speak-during-execution)
   - HMAC signature verification on outgoing webhooks
4. Pre-built tools: end call, transfer call (cold via SIP REFER)
5. Dynamic variables: `{{variable}}` substitution in prompts, set via API
6. Inbound call routing: dispatch rules mapping phone numbers to agents
7. Outbound call API: `POST /calls` with `to`, `from`, `agent_id`, `variables`
8. Full REST API: CRUD for agents, phone numbers, calls
9. Webhook system: `call_started`, `call_ended` events

**Success criteria:** Configure an agent via API that can book an appointment by calling an external HTTP endpoint mid-conversation.

---

### Phase 3 — Knowledge Base & Conversation State
**Goal:** Agents answer questions from uploaded documents; conversations have memory.
**Estimate:** 2-3 weeks

**Deliverables:**
1. Knowledge base CRUD: create KB, upload documents (PDF, DOCX, TXT, MD, CSV, HTML)
2. Document processing: chunk -> embed -> store in pgvector
3. Retrieval: on each user turn, embed transcript -> cosine similarity -> inject top-K chunks
4. KB assignment per agent (retrieval is automatic when KB is assigned)
5. URL sources with auto-refresh (crawl every 24h via background job)
6. Conversation context management: message accumulation, summarization for long calls
7. Warm call transfer: create second call, provide context, bridge

**Success criteria:** Upload a product FAQ, call the agent, ask about it, get accurate answers.

---

### Phase 4 — Web Dashboard & Playground
**Goal:** Non-developer can create and test agents in a browser.
**Estimate:** 3-4 weeks

**Deliverables:**
1. React dashboard: auth (JWT), navigation, agent list
2. Agent builder: prompt editor, voice picker, LLM selection, tool configuration
3. Web playground: browser WebRTC via LiveKit client SDK -> live conversation test
4. Call history: filterable table, click for detail view
5. Call detail: full transcript, latency metrics, recording playback
6. Phone number management: buy via Telnyx API, assign to agent
7. Knowledge base management: create, upload, see indexing status

**Success criteria:** Non-developer creates an agent, tests it in the browser, assigns it to a phone number — all via UI.

---

### Phase 5 — Conversation Flow Builder
**Goal:** Visual flow builder for structured conversations.
**Estimate:** 4-6 weeks

This is the most complex phase and Retell's most differentiated feature.

**Deliverables:**
1. Flow data model: directed graph stored as JSON in PostgreSQL
2. React Flow visual editor with drag-and-drop node palette
3. Initial node types (6 of Retell's 11):
   - **Conversation Node** — prompt + transition conditions
   - **Function Node** — trigger tool, optional wait-for-result
   - **Logic Split Node** — branch on variable conditions
   - **End Node** — farewell + hang up
   - **Call Transfer Node** — transfer to number
   - **Extract Variable Node** — LLM extracts data into variable
4. Transition system:
   - Equation conditions: `==`, `!=`, `>`, `<`, `AND`, `OR`, `CONTAINS`
   - Prompt conditions: LLM-evaluated natural language
   - Evaluation order: equations first (top-to-bottom), then prompts
5. Flow runtime engine: state machine traversing the graph with per-node context
6. Per-node LLM and voice selection
7. Flow versioning: immutable published versions, draft editing

**Deferred:** Global nodes, reusable components, flex mode, SMS node, MCP node, press digit node, finetune examples.

**Success criteria:** Build a 5-node appointment booking flow in the visual editor, deploy it, call the number, complete the flow.

---

### Phase 6 — Monitoring, Analytics & Call Analysis
**Goal:** Post-call insights, dashboards, alerting.
**Estimate:** 3-4 weeks

**Deliverables:**
1. Post-call analysis pipeline (on `call_ended`, LLM analyzes transcript):
   - Summary
   - Success/failure (configurable criteria per agent)
   - User sentiment
   - Custom extractors (boolean/text/number/selector)
2. Analytics API: aggregated metrics (volume, duration, success rate, latency p50/p90/p99)
3. Dashboard: charts (volume over time, success rate, latency), filterable by agent and date
4. Per-call latency breakdown: timestamps at each pipeline stage
5. Webhook expansion: `call_analyzed` event
6. Basic alerting: threshold rules on metrics, email/webhook notification

**Success criteria:** Dashboard shows trends, per-call drill-down shows full analysis and latency breakdown.

---

### Phase 7 — Simulation Testing & QA
**Goal:** Automated testing of agents before and after deployment.
**Estimate:** 3-4 weeks

**Deliverables:**
1. Test case model: simulated user with identity, goal, personality
2. Simulation engine: LLM-as-user calls agent via internal API (no actual phone call)
3. Evaluation framework (LLM-as-judge):
   - Task completion
   - Function execution correctness
   - Response quality (tone, relevance)
4. Batch simulation: N test cases in parallel, aggregated results
5. Function mocking: deterministic tool responses for testing
6. QA cohorts: sample X% of production calls, evaluate, surface failures

**Success criteria:** Define 10 test scenarios, batch-run them, see pass/fail report with scores.

---

### Phase 8 — Multi-Tenant & Enterprise
**Goal:** Multiple organizations, access control, compliance foundations.
**Estimate:** 4-6 weeks

**Deliverables:**
1. Workspace model: organization -> users -> agents -> calls
2. RBAC: admin, developer, viewer
3. API key management per workspace
4. PII scrubbing: configurable redaction in transcripts
5. Data retention policies (auto-delete after N days)
6. Usage metering per component (STT, LLM, TTS, telephony minutes)
7. Concurrency management per workspace
8. SMS channel: two-way via Telnyx
9. Chat widget: embeddable web chat using same agent logic

---

## 5. What We're NOT Building

| Retell Feature | Rationale |
|---|---|
| Proprietary voices from performance data | Use Kokoro + commercial TTS providers |
| SOC2/HIPAA certification | Build the controls; leave certification to deployers |
| Branded caller ID / spam verification | Carrier-level feature, handled by trunk provider |
| 50+ language STT modes | Start English + Spanish, expand per demand |
| Ambient sound injection | Trivial audio mixing, add when requested |
| A/B testing for agents | Simple traffic-splitting, add when there's demand |
| Air-gapped deployment | Architecture supports it; don't optimize for it early |
| Chat-specific agent type | Reuse voice agents in text mode initially |

---

## 6. Technical Risks

| Risk | Impact | Mitigation |
|---|---|---|
| **STT latency** | Whisper adds 1-2s vs. Deepgram. Agents feel slow. | Deepgram for prod ($2.60/1000min). Ultravox as future path. |
| **Turn-taking quality** | False interruptions or slow responses ruin UX | LiveKit's transformer model + 2-3 weeks tuning per language |
| **Flow engine complexity** | State machine with LLM-evaluated transitions is hard | Start with 6 nodes, equation conditions only. Add prompt conditions after proving engine |
| **LiveKit SIP maturity** | Less battle-tested than FreeSWITCH for edge cases | Telnyx WebSocket as fallback. FreeSWITCH escape hatch for enterprise |
| **Kokoro TTS quality** | May not match ElevenLabs for all voices/languages | Pluggable TTS. Add commercial providers as options |
| **GPU costs** | RTX 4090 needed for fully local stack | Cloud STT/TTS makes GPU optional for small deployments |
| **Concurrent call scaling** | Each call = agent process + inference | Horizontal workers, shared LLM via vLLM, Kokoro is lightweight |

---

## 7. Success Metrics

| Milestone | Definition |
|---|---|
| **Phase 1 complete** | First phone call with an AI agent works end-to-end |
| **Phase 4 complete** | Non-developer can create + deploy an agent from the browser |
| **Phase 5 complete** | Visual flow builder works for structured conversations |
| **v1.0** | Phases 1-6 complete. Usable platform for building voice agents |
| **Production-ready** | Phases 1-8 complete. Multi-tenant, tested, monitored |

---

## 8. Open Questions

1. **Hosting model** — Docker Compose for single-server? Helm chart for K8s? Both?
2. **Plugin system** — should custom STT/TTS/LLM integrations be plugins or code changes?
3. **Pricing/billing** — should Pipesong include usage metering for resellers, or leave that to deployers?
4. **Voice cloning** — XTTS-v2 (MPL-2.0) has copyleft license implications. Fish Speech or F5-TTS (both permissive) may be better defaults.
5. **Ultravox timeline** — when is audio-native LLM mature enough to replace the STT -> LLM pipeline?
