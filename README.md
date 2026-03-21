# Pipesong

A cost-efficient, low-latency voice AI engine for handling hundreds to thousands of phone calls with topic-trained AI agents.

**Target: <$0.03/min and <1s response latency** — vs Retell AI's $0.07-0.31/min.

## What It Does

Give Pipesong a system prompt, a knowledge base, and a phone number. It answers calls with an AI agent that sounds human, knows the topic, and can take actions.

```
POST /agents
{
  "name": "Soporte Técnico",
  "system_prompt": "Eres un agente de soporte para MiEmpresa...",
  "language": "es",
  "voice": "kokoro_es_male_1",
  "knowledge_base": { "documents": ["https://miempresa.com/faq", "manual.pdf"] },
  "tools": [{ "name": "create_ticket", "endpoint": "https://api.example.com/tickets", ... }],
  "flow": { "initial_state": "greeting", "states": { ... } }
}
→ Agent is live on +52 55 1234 5678
```

## Why Not Retell AI?

| | Retell AI | Pipesong |
|---|---|---|
| Cost at 120K min/mo | $8,400-36,000 | ~$2,500 |
| Per minute | $0.07-0.30 | ~$0.02-0.03 |
| Response latency (p50) | ~600ms-1.5s | ~900ms-1,100ms |
| Data | Their cloud | Your servers |
| Single point of failure | Their platform | Each component has fallback |
| Vendor lock-in | Yes | No |

Savings come from running LLM and TTS locally. STT stays on Deepgram (local Whisper costs the same but adds 500-1000ms latency).

## Architecture

Two stages: start simple, graduate when you need to.

### Stage 1: Pipecat + Telnyx (up to ~20 concurrent calls)

```
Telnyx (PSTN + phone numbers + WebSocket audio)
  │
  ▼
Python App (Pipecat pipeline + FastAPI)
  │
  ├─ Deepgram STT (cloud, streaming, 150-300ms)
  │   └─ fallback: local faster-whisper on Deepgram failure
  │
  ├─ LLM → vLLM server (local GPU)
  │   └─ overflow: Groq API when GPU is saturated
  │
  └─ TTS → Kokoro server (local GPU)
       └─ fallback: ElevenLabs on GPU failure

Storage: PostgreSQL + pgvector | MinIO
```

### Stage 2: LiveKit (30+ concurrent calls)

Same pipeline code, swap transport layer via Pipecat's `LiveKitTransport`:

```
Telnyx SIP Trunk → LiveKit SIP → LiveKit Server → Agent Workers → GPU Servers
```

Adds: SIP trunk flexibility, job dispatch, Redis clustering, multi-node scaling.

## Core Stack

| Component | Technology | Why This |
|---|---|---|
| Pipeline | [Pipecat](https://github.com/pipecat-ai/pipecat) | Python library, no infra to deploy. Telnyx serializer built-in. |
| STT | [Deepgram Nova-3](https://deepgram.com) + local [faster-whisper](https://github.com/SYSTRAN/faster-whisper) fallback | 150-300ms streaming + resilience |
| LLM | [vLLM](https://github.com/vllm-project/vllm) + Groq overflow | Local continuous batching + cloud overflow at peak |
| TTS | [Kokoro](https://github.com/hexgrad/kokoro) (or Fish Speech) | 50-150ms TTFB, 0.5 GB VRAM |
| Turn detection | [LiveKit turn-detector](https://huggingface.co/livekit/turn-detector) or Pipecat Smart Turn | CPU, 14 languages, 85% fewer false interruptions |
| Telephony | [Telnyx](https://telnyx.com) | $0.007/min, WebSocket audio, good LATAM coverage |
| Knowledge base | PostgreSQL + pgvector + local embeddings | No separate vector DB |
| Flows | YAML-defined state machines, interpreted at runtime | API-deployable, no code changes needed |

## Latency Strategy

Naive pipelines (STT → LLM → TTS sequential) land at 1.5-2.5 seconds. Pipesong uses proven overlap techniques:

- **Sentence-level TTS streaming** — each LLM sentence streams to TTS immediately while the next generates
- **Pre-cached responses** — common phrases ("Un momento por favor") play with 0ms TTS
- **First-word priority** — LLM starts with short acknowledgment ("Claro, ...") while full answer generates
- **Warm connections** — persistent WebSocket/HTTP to all services, zero handshake overhead
- **Smart turn detection** — transformer model adjusts silence timeout by linguistic context

Result: **p50 ~900-1,100ms, p95 ~1,200-1,500ms**

## Failure Resilience

| Failure | What Happens |
|---|---|
| Deepgram down | Automatic switch to local faster-whisper (+500-1000ms latency, but calls continue) |
| GPU overloaded | New calls overflow to Groq API (LLM) automatically |
| GPU crash | Emergency cloud fallback (Groq + ElevenLabs) at ~$0.05/min |
| Telnyx issues | Manual failover to Twilio backup trunk |

## Hardware Requirements

**For 10-20 concurrent calls (Stage 1):**
- 1× GPU server: 1× RTX 4090, 32 GB RAM, 8-core CPU
- vLLM: ~8 GB VRAM, Kokoro: ~0.5 GB, faster-whisper fallback: ~4 GB

**For 30-50 concurrent calls (Stage 2):**
- 1× GPU server: 2× RTX 4090, 64 GB RAM, 16-core CPU
- + LiveKit Server, SIP service, Redis

## Roadmap

| Phase | What | Duration |
|---|---|---|
| 0 | Validate LLM, TTS, and turn detector in Spanish | 1 week |
| 1 | First phone call with AI agent | 2-3 weeks |
| 2 | Multi-agent routing, tools, webhooks | 2-3 weeks |
| 3 | Knowledge base (RAG) | 2 weeks |
| 4 | Latency optimization, conversation flows | 4-6 weeks |
| 5 | Call analysis, monitoring, Grafana | 2 weeks |
| 6 | Scale hardening, batch calling, load testing | 3-4 weeks |

**Total: 16-21 weeks.** See [PLAN.md](PLAN.md) for full details.

## Status

**Pre-alpha.** Phase 0 (benchmarking) is next.

## License

MIT — Copyright (c) 2026 VoxPopulai
