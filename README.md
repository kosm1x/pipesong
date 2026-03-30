# Pipesong

A cost-efficient, low-latency voice AI engine for handling hundreds to thousands of phone calls with topic-trained AI agents.

**Target: <$0.03/min and <1s response latency** — vs Retell AI's $0.07-0.31/min.

## Current Status

**Phase 4a IN PROGRESS — All code complete, GPU validation pending (2026-03-30).**

Phases 0-3 done and verified with live calls. Phase 4a adds per-turn latency instrumentation, Spanish-aware sentence streaming, ToolCallProcessor early bail-out, STTMuteFilter for disclosure/tool execution, and per-agent VAD tuning. QA audit (11 findings) resolved.

Measured latency: Deepgram STT 260ms + Qwen LLM 120ms + Kokoro TTS 450ms = **~830ms total**.

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

|                        | Retell AI     | Pipesong                              |
| ---------------------- | ------------- | ------------------------------------- |
| Cost at 120K min/mo    | $8,400-36,000 | ~$2,500                               |
| Per minute             | $0.07-0.30    | ~$0.02-0.03                           |
| Response latency (p50) | ~600ms-1.5s   | **~830ms** (under 1s target achieved) |
| Data                   | Their cloud   | Your servers                          |
| Vendor lock-in         | Yes           | No                                    |

Savings come from running LLM and TTS locally. STT stays on Deepgram (local Whisper costs the same but adds 500-1000ms latency).

## Architecture

```
Phone Call → Telnyx (PSTN + WebSocket audio)
  │
  ▼
FastAPI + Pipecat Pipeline
  │
  ├─ Deepgram STT (cloud, streaming, 220ms)
  ├─ STTMuteFilter (suppress interruption during disclosure/tools)
  ├─ RAGProcessor (pgvector KB retrieval, 11-32ms)
  ├─ Qwen 2.5 7B AWQ via vLLM (local GPU, 110ms TTFB)
  ├─ ToolCallProcessor (streaming mode + early bail-out)
  ├─ SpanishOnlyFilter (strips CJK from Qwen output)
  ├─ SentenceStreamBuffer (Spanish-aware sentence boundaries)
  ├─ Kokoro TTS em_alex (local, 389-554ms in pipeline)
  ├─ MetricsCollector (per-turn TTFB → call_latency table)
  ├─ AudioBufferProcessor (call recording → WAV → MinIO)
  └─ Silero VAD + Pipecat Smart Turn v3

Storage: PostgreSQL (agents, calls, transcripts) + MinIO (recordings)
```

## Core Stack

| Component      | Technology                                                           | Measured Performance                           |
| -------------- | -------------------------------------------------------------------- | ---------------------------------------------- |
| Pipeline       | [Pipecat 0.0.106](https://github.com/pipecat-ai/pipecat)             | Telnyx WebSocket serializer, Smart Turn v3     |
| STT            | [Deepgram Nova-3](https://deepgram.com)                              | 234-269ms TTFB, Spanish streaming              |
| LLM            | [vLLM 0.6.6](https://github.com/vllm-project/vllm) + Qwen 2.5 7B AWQ | 118-130ms TTFB                                 |
| TTS            | [Kokoro](https://github.com/hexgrad/kokoro) `em_alex`                | **389-554ms TTFB** (clause-split optimization) |
| Turn detection | Pipecat Smart Turn v3                                                | Audio-based, working on real calls             |
| Telephony      | [Telnyx](https://telnyx.com)                                         | +12678840093 (US), $1/month, TeXML WebSocket   |
| VAD            | Silero VAD                                                           | CPU, <10ms                                     |

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

| Phase | What                                         | Status                                                                                            |
| ----- | -------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| 0     | Validate LLM, TTS, turn detector in Spanish  | **Done**                                                                                          |
| 1     | First phone call with AI agent               | **Done** — disclosure + transcript + recording + STT error logging                                |
| 2     | Multi-agent routing, tools, webhooks         | **Done** — tool calling + webhooks (HMAC) + outbound calls + end_call/transfer_call + audit fixes |
| 3     | Knowledge base (RAG)                         | **Done** — pgvector + multilingual-e5-small, 11-32ms retrieval, KB CRUD + document upload         |
| 4a    | Latency optimization                         | **In progress** — code done, GPU validation pending (3 items)                                     |
| 4b    | Conversation flows                           | Not started                                                                                       |
| 5     | Call analysis, monitoring, Grafana           | Not started                                                                                       |
| 6     | Scale hardening, batch calling, load testing | Not started                                                                                       |

**Total: 17-22 weeks.** See [PLAN.md](PLAN.md) for full details, [PROGRESS.md](docs/PROGRESS.md) for activity tracking.

## Known Issues

| Issue                                  | Status     | Impact                                          |
| -------------------------------------- | ---------- | ----------------------------------------------- |
| Kokoro TTS latency                     | **Solved** | Comma→period trick: 2.3s → 450ms                |
| Qwen 2.5 Chinese code-switching        | Mitigated  | SpanishOnlyFilter strips CJK + fixes spaces     |
| Kokoro prosody (pauses at punctuation) | Open       | Minor — pronunciation good, pacing needs tuning |

## License

MIT — Copyright (c) 2026 VoxPopulai
