# Pipesong

A cost-efficient, low-latency voice AI engine for handling hundreds to thousands of phone calls with topic-trained AI agents.

**Target: <$0.02/min and <800ms response latency** — vs Retell AI's $0.07-0.30/min.

## What It Does

You give Pipesong a system prompt, a knowledge base, and a phone number. It answers calls with an AI agent that sounds human, knows the topic, and can take actions (book appointments, check status, transfer calls).

```
POST /agents
{
  "name": "Soporte Técnico",
  "system_prompt": "Eres un agente de soporte para MiEmpresa...",
  "language": "es",
  "voice": "kokoro_es_male_1",
  "knowledge_base": { "documents": ["https://miempresa.com/faq", "manual.pdf"] },
  "tools": [{ "name": "create_ticket", "endpoint": "https://api.example.com/tickets", ... }]
}
→ Agent is live on +52 55 1234 5678 in <60 seconds
```

## Why Not Retell AI?

| | Retell AI | Pipesong |
|---|---|---|
| Cost at 120K min/mo | $8,400-36,000 | ~$2,256 |
| Per minute | $0.07-0.30 | ~$0.019 |
| Response latency | ~600ms-1.5s | Target <800ms |
| Infrastructure | Their cloud | Your servers |
| Data | Their storage | Your storage |
| Vendor lock-in | Yes | No |

The savings come from running LLM and TTS locally on GPU. STT stays on Deepgram (streaming latency at this price point is unbeatable).

## Architecture

```
Telnyx SIP Trunk
  │
  ▼
LiveKit SIP Service ──► LiveKit Server ──► Agent Workers (CPU)
                              │                    │
                            Redis            ┌─────┴─────┐
                                             ▼           ▼
                                    GPU Server(s)    Deepgram STT
                                    ┌─────────┐      (cloud,
                                    │ vLLM    │       streaming)
                                    │ Kokoro  │
                                    └─────────┘
                                         │
                                    PostgreSQL + pgvector + MinIO
```

- **Agent workers** are CPU-only — they handle call logic, not inference
- **Model servers** (LLM + TTS) run on shared GPU with batching
- **STT** is Deepgram cloud (150-300ms streaming, $0.0043/min)
- Scaling = add more CPU workers. GPU is the shared bottleneck, handled by vLLM continuous batching

## Core Stack

| Component | Technology | Why |
|---|---|---|
| Agent framework | [LiveKit Agents](https://github.com/livekit/agents) | Built-in SIP, dispatch, scaling, turn detection |
| STT | [Deepgram Nova-3](https://deepgram.com) | 150-300ms streaming. Local Whisper adds 1-2s for same cost. |
| LLM | [vLLM](https://github.com/vllm-project/vllm) + Qwen 2.5 7B | Local, continuous batching, ~$0/min |
| TTS | [Kokoro](https://github.com/hexgrad/kokoro) | 50-150ms TTFB, 0.5 GB VRAM, Apache 2.0 |
| Turn detection | [LiveKit turn-detector](https://huggingface.co/livekit/turn-detector) | CPU, 50-160ms, 14 languages, 85% fewer false interruptions |
| Telephony | [Telnyx](https://telnyx.com) | $0.007/min, SIP trunk, good LATAM coverage |
| Knowledge base | PostgreSQL + pgvector | No separate vector DB needed |

## Latency Strategy

Standard voice AI pipelines run sequentially (STT → LLM → TTS) at 1.5-2.5 seconds. Pipesong overlaps them:

```
VAD endpoint:     [====]
STT (streaming):  [===========]          ← starts during VAD silence
LLM (speculative):      [=====]          ← starts on interim transcript
TTS (streaming):              [====]     ← starts on first sentence
Audio plays:                       [→    ← user hears response at ~700-900ms
```

- STT processes audio before VAD confirms end-of-speech
- LLM starts generating on partial transcripts
- TTS streams each sentence as the LLM produces it
- Pre-cached audio for common phrases (0ms TTS)

## Hardware Requirements

**For 30 concurrent calls:**
- 1× GPU server: 2× RTX 4090, 64 GB RAM, 16-core CPU
- vLLM: ~8 GB VRAM (Qwen 7B AWQ)
- Kokoro: ~0.5 GB VRAM
- Agent workers: CPU-only, 1 GB RAM each

**For 50-100 concurrent calls:**
- 2× GPU servers (or 4× RTX 4090)
- More CPU workers

## Status

**Pre-alpha.** See [PLAN.md](PLAN.md) for the full technical plan.

## License

MIT
