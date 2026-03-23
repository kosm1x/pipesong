# Pipesong

A cost-efficient, low-latency voice AI engine for handling hundreds to thousands of phone calls with topic-trained AI agents.

**Target: <$0.03/min and <1s response latency** — vs Retell AI's $0.07-0.31/min.

## Current Status

**Phase 1 — First phone conversation achieved (2026-03-23).**

A real caller dialed in, spoke Spanish with the AI agent for ~10 turns, troubleshot an internet connection problem (router reset), and said goodbye. Full pipeline working end-to-end.

Measured latency: Deepgram STT 220ms + Qwen LLM 110ms + Kokoro TTS 800-1600ms.

See [PROGRESS.md](docs/PROGRESS.md) for detailed status.

## What It Does

Give Pipesong a system prompt, a knowledge base, and a phone number. It answers calls with an AI agent that sounds human, knows the topic, and can take actions.

```
POST /agents
{
  "name": "Soporte Técnico",
  "system_prompt": "Eres un agente de soporte para MiEmpresa...",
  "language": "es",
  "voice": "em_alex",
  "phone_number": "+12678840093",
  "disclosure_message": "Esta llamada está siendo grabada para fines de calidad."
}
→ Agent is live. Call the number.
```

## Why Not Retell AI?

| | Retell AI | Pipesong |
|---|---|---|
| Cost at 120K min/mo | $8,400-36,000 | ~$2,500 |
| Per minute | $0.07-0.30 | ~$0.02-0.03 |
| Response latency (p50) | ~600ms-1.5s | ~1-2s (unoptimized, Phase 4 targets <1s) |
| Data | Their cloud | Your servers |
| Vendor lock-in | Yes | No |

Savings come from running LLM and TTS locally. STT stays on Deepgram (local Whisper costs the same but adds 500-1000ms latency).

## Architecture

```
Phone Call → Telnyx (PSTN + WebSocket audio)
  │
  ▼
FastAPI + Pipecat Pipeline
  │
  ├─ Deepgram STT (cloud, streaming, 220ms)
  ├─ Qwen 2.5 7B AWQ via vLLM (local GPU, 110ms TTFB)
  ├─ SpanishOnlyFilter (strips CJK from Qwen output)
  ├─ Kokoro TTS em_alex (local, 800-1600ms in pipeline)
  └─ Silero VAD + Pipecat Smart Turn v3

Storage: PostgreSQL (agents, calls, transcripts) + MinIO (recordings)
```

## Core Stack

| Component | Technology | Measured Performance |
|---|---|---|
| Pipeline | [Pipecat 0.0.106](https://github.com/pipecat-ai/pipecat) | Telnyx WebSocket serializer, Smart Turn v3 |
| STT | [Deepgram Nova-3](https://deepgram.com) | 220-270ms TTFB, Spanish streaming |
| LLM | [vLLM 0.6.6](https://github.com/vllm-project/vllm) + Qwen 2.5 7B AWQ | 110ms TTFB, 130ms @20 concurrent |
| TTS | [Kokoro](https://github.com/hexgrad/kokoro) `em_alex` | 115ms standalone, 800-1600ms in pipeline |
| Turn detection | Pipecat Smart Turn v3 | Audio-based, working on real calls |
| Telephony | [Telnyx](https://telnyx.com) | +12678840093 (US), $1/month, TeXML WebSocket |
| VAD | Silero VAD | CPU, <10ms |

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/kosm1x/pipesong
cd pipesong
cp .env.example .env  # Fill in API keys

# 2. Start PostgreSQL + MinIO
docker compose up -d

# 3. Start vLLM (needs GPU)
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-7B-Instruct-AWQ \
  --quantization awq --port 8000

# 4. Start Pipesong
PYTHONPATH=src python -m uvicorn pipesong.main:app --host 0.0.0.0 --port 8080

# 5. Create an agent
curl -X POST http://localhost:8080/agents \
  -H "Content-Type: application/json" \
  -d '{"name":"Test","system_prompt":"Eres un agente amable...","language":"es","voice":"em_alex","phone_number":"+1XXXXXXXXXX","disclosure_message":"Esta llamada está siendo grabada."}'

# 6. Point Telnyx TeXML webhook to http://YOUR_IP:8080/telnyx/webhook
# 7. Call the number
```

## Roadmap

| Phase | What | Status |
|---|---|---|
| 0 | Validate LLM, TTS, turn detector in Spanish | **Done** |
| 1 | First phone call with AI agent | **60%** — conversation works, storage pending |
| 2 | Multi-agent routing, tools, webhooks | Not started |
| 3 | Knowledge base (RAG) | Not started |
| 4 | Latency optimization, conversation flows | Not started |
| 5 | Call analysis, monitoring, Grafana | Not started |
| 6 | Scale hardening, batch calling, load testing | Not started |

**Total: 16-21 weeks.** See [PLAN.md](PLAN.md) for full details, [PROGRESS.md](docs/PROGRESS.md) for activity tracking.

## Known Issues

| Issue | Status | Impact |
|---|---|---|
| Kokoro TTS latency in pipeline (800-1600ms vs 115ms standalone) | Open | Main latency bottleneck |
| Qwen 2.5 switches to Chinese mid-response | Mitigated | SpanishOnlyFilter strips CJK before TTS |
| No call recording or transcript persistence yet | Phase 1 remaining | Data not stored after calls |
| No recording disclosure plays | Phase 1 remaining | Legal requirement for Mexico |

## License

MIT — Copyright (c) 2026 VoxPopulai
