# Pipesong

An open-source, self-hostable voice AI platform for building, testing, deploying, and monitoring phone agents.

Think "self-hosted Retell AI" — not just a framework, but the full product: visual flow builder, agent management, telephony, testing, analytics, and multi-tenant API.

## Why Pipesong?

Commercial voice AI platforms (Retell AI, VAPI, Bland) charge $0.07-0.30/minute and lock you into their infrastructure. Open-source frameworks (Pipecat, LiveKit Agents) give you the pipeline but not the platform — no UI, no agent management, no analytics, no testing harness.

Pipesong fills the gap: a complete platform you own and operate.

## Architecture

```
                    +----------------------------------+
                    |        React Dashboard           |
                    |  (Flow Builder, Playground,      |
                    |   Agent Config, Analytics)       |
                    +----------------+-----------------+
                                     | REST / WebSocket
                    +----------------v-----------------+
                    |          FastAPI Server           |
                    |  (Agent CRUD, Call Management,    |
                    |   Webhooks, Analytics API)        |
                    +---+----------+----------+--------+
                        |          |          |
               +--------v--+ +----v----+ +---v----------+
               | PostgreSQL | |  Redis  | |    MinIO     |
               | (+ pgvector)| |         | | (recordings) |
               +------------+ +----+----+ +--------------+
                                   |
                    +--------------v-------------------+
                    |      LiveKit Server (SFU)        |
                    |    + LiveKit SIP Service          |
                    +---------+---------------+--------+
                              |               |
                    +---------v------+ +------v--------+
                    |  SIP Trunk     | | WebRTC Client |
                    |  (Telnyx)      | | (Playground)  |
                    +--------+-------+ +---------------+
                             |
                    +--------v-------------------------+
                    |       Agent Workers (Python)      |
                    |    LiveKit AgentSession per call   |
                    |    +-----+ +-----+ +------+      |
                    |    | STT | | LLM | | TTS  |      |
                    |    +-----+ +-----+ +------+      |
                    +----------------------------------+
```

## Core Stack

| Component | Technology | License |
|---|---|---|
| Agent framework | [LiveKit Agents](https://github.com/livekit/agents) | Apache 2.0 |
| Media server | [LiveKit Server](https://github.com/livekit/livekit) | Apache 2.0 |
| Telephony | LiveKit SIP + Telnyx trunk | Apache 2.0 |
| STT | [Deepgram](https://deepgram.com) (prod) / [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (self-hosted) | MIT |
| TTS | [Kokoro](https://github.com/hexgrad/kokoro) (primary) / [XTTS-v2](https://github.com/idiap/coqui-ai-TTS) (voice cloning) | Apache 2.0 / MPL-2.0 |
| LLM | Provider-agnostic (OpenAI-compatible) / [Ollama](https://github.com/ollama/ollama) (local) | MIT |
| Turn detection | [LiveKit turn-detector](https://huggingface.co/livekit/turn-detector) (Qwen2.5-0.5B) | Apache 2.0 |
| VAD | [Silero VAD](https://github.com/snakers4/silero-vad) | MIT |
| API server | FastAPI | MIT |
| Database | PostgreSQL + pgvector | PostgreSQL License |
| Cache / state | Redis | BSD-3 |
| Object storage | MinIO | AGPL-3.0 |
| Frontend | React + TypeScript + React Flow | MIT |

## Features (Planned)

### Build
- **Single-prompt agents** — one system prompt defines behavior
- **Conversation flow agents** — visual node-and-edge graph builder
  - Conversation, Function, Logic Split, End, Call Transfer, Extract Variable nodes
  - LLM-evaluated and equation-based transition conditions
  - Per-node LLM and voice selection for cost optimization
- **Function calling** — agents invoke HTTP endpoints mid-conversation (sync or async)
- **Knowledge base** — upload documents, automatic RAG retrieval during calls
- **Dynamic variables** — `{{variable}}` substitution in prompts, set per-call via API

### Test
- **Web playground** — test agents in the browser via WebRTC
- **Phone test** — test via actual phone call
- **Simulation testing** — LLM-as-user with configurable identity, goal, personality
- **Batch simulation** — run multiple test scenarios in parallel

### Deploy
- **Inbound calls** — assign agents to phone numbers
- **Outbound calls** — trigger calls via API
- **Batch calling** — CSV upload for outbound campaigns
- **Call transfer** — cold and warm transfers
- **DTMF handling** — detect and send button presses
- **Omnichannel** — voice, web chat, SMS (planned)

### Monitor
- **Webhooks** — call lifecycle events (started, ended, analyzed)
- **Call analysis** — LLM-powered post-call summary, sentiment, success/failure, custom extractors
- **Analytics dashboard** — call volume, latency breakdown (p50/p90/p99), success rates
- **Alerting** — threshold-based alerts on key metrics

### Enterprise (Later Phases)
- Multi-tenant workspaces with RBAC (admin, developer, viewer)
- PII scrubbing (names, emails, phones, SSNs)
- Configurable data retention policies
- Usage metering per component

## Latency Targets

| Configuration | Expected Latency |
|---|---|
| Deepgram + cloud LLM + Kokoro | ~1.0-1.5s |
| faster-whisper + Ollama + Kokoro (fully local) | ~1.3-2.0s |
| Ultravox (audio-native LLM) + Kokoro | ~0.8-1.3s |

Measured as: user stops speaking → hears first word of response.

## Self-Hosting Requirements

**Minimum (cloud STT/TTS):**
- 4-core CPU, 8 GB RAM
- PostgreSQL, Redis
- Telnyx or Twilio account for phone numbers

**Recommended (fully local AI):**
- 8+ core CPU, 32 GB RAM
- NVIDIA GPU with 16+ GB VRAM (RTX 4090 recommended)
- All models fit on a single GPU: faster-whisper (~4 GB) + LLM (~5 GB) + Kokoro (~0.5 GB)

## Status

**Pre-alpha.** See [PLAN.md](PLAN.md) for the full roadmap.

## License

MIT
