# Phase 1 — First Phone Call (Detailed Plan)

## Goal

Call a phone number → AI answers with recording disclosure → converses in Spanish → hangs up. Transcript and recording stored in PostgreSQL + MinIO.

## Exit Criteria

1. Dial a Telnyx number from a real phone
2. Hear "Esta llamada está siendo grabada para fines de calidad"
3. Have a 3-minute conversation in Spanish with the AI agent
4. Agent responds naturally, handles basic Q&A
5. After hangup, verify: transcript in PostgreSQL, recording in MinIO
6. Latency feels conversational (not measured precisely — that's Phase 4)

---

## Architecture

```
Your Phone → PSTN → Telnyx (SIP termination)
                        │
                        │ TeXML webhook → our FastAPI responds with <Stream>
                        │
                        ▼
                   WebSocket (8kHz PCMU audio)
                        │
                        ▼
              FastAPI + Pipecat Pipeline (TensorDock or VPS)
                │
                ├─ Silero VAD (CPU, <10ms)
                ├─ Deepgram STT (cloud WebSocket, 150-300ms)
                ├─ LLM Context Aggregator
                ├─ vLLM / Qwen 2.5 7B AWQ (local GPU, 130ms TTFT)
                ├─ Kokoro TTS em_alex (local GPU, 115ms TTFB)
                └─ Audio back to Telnyx → caller hears response

Storage:
  PostgreSQL — agents, calls, transcripts
  MinIO — call recordings (WAV)
```

## Components

### 1. Telnyx Setup

**Account requirements:**
- Telnyx account with Voice API enabled
- Buy 1 phone number (US or Mexico, ~$1/month)
- Create a TeXML Application pointing to our webhook URL
- API key for call control

**TeXML webhook response (our server returns this on incoming call):**
```xml
<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="wss://OUR_SERVER/ws" bidirectionalMode="rtp" />
  </Connect>
  <Pause length="600"/>
</Response>
```

**Key detail:** Telnyx sends call metadata (from/to numbers, stream_id, call_control_id) in the WebSocket connect message. No separate webhook needed for call routing in Phase 1 (single agent).

### 2. Pipecat Pipeline

**Core pipeline (7 processors in sequence):**
```
transport.input() → STT → user_aggregator → LLM → TTS → transport.output() → assistant_aggregator
```

**Key classes:**
- `FastAPIWebsocketTransport` + `TelnyxFrameSerializer` — handles audio I/O over Telnyx WebSocket
- `DeepgramSTTService` — streaming STT, Spanish, interim results
- `OpenAILLMService` — pointed at local vLLM via `base_url`
- `KokoroTTSService` — local TTS with `em_alex` voice
- `SileroVADAnalyzer` — voice activity detection
- `LLMContextAggregatorPair` — manages conversation context

**Audio config:** 8kHz in/out (matches Telnyx PCMU). Kokoro generates at 24kHz internally and resamples.

### 3. GPU Services (TensorDock)

All three model servers run on the same RTX 4090:

| Service | Port | VRAM | Startup Command |
|---|---|---|---|
| vLLM (Qwen 2.5 7B AWQ) | 8000 | ~5 GB | `python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-7B-Instruct-AWQ --quantization awq --port 8000 --max-model-len 4096` |
| Kokoro TTS | 8880 | ~0.5 GB | `docker run --gpus all -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-gpu` |
| faster-whisper (fallback) | — | ~4 GB | Loaded in Pipecat process via `WhisperSTTService`, activated only on Deepgram failure |

**Total VRAM: ~10 GB out of 24 GB.** Comfortable headroom.

**Note:** Kokoro runs inside the Pipecat process natively (not as a separate server) when using `KokoroTTSService`. The Docker approach is optional for Phase 1. Native is simpler.

### 4. Data Storage

**PostgreSQL schema:**
```sql
CREATE TABLE agents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    language VARCHAR(5) DEFAULT 'es',
    voice VARCHAR(50) DEFAULT 'em_alex',
    phone_number VARCHAR(20),
    disclosure_message TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID REFERENCES agents(id),
    from_number VARCHAR(20),
    to_number VARCHAR(20),
    started_at TIMESTAMPTZ DEFAULT now(),
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    recording_url TEXT,
    status VARCHAR(20) DEFAULT 'in_progress'
);

CREATE TABLE transcripts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    call_id UUID REFERENCES calls(id),
    role VARCHAR(20) NOT NULL,  -- 'user' or 'assistant'
    content TEXT NOT NULL,
    timestamp_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

**MinIO:** S3-compatible object storage for call recordings. Bucket: `pipesong-recordings`. Key format: `{call_id}.wav`.

### 5. FastAPI Server

**Endpoints (Phase 1 — minimal):**

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/agents` | Create agent (name, system_prompt, language, disclosure_message) |
| `GET` | `/agents` | List agents |
| `GET` | `/agents/{id}` | Get agent detail |
| `POST` | `/telnyx/webhook` | TeXML webhook — returns `<Stream>` XML pointing to our WebSocket |
| `WS` | `/ws` | Pipecat WebSocket endpoint — Telnyx connects here for audio |
| `GET` | `/calls` | List calls |
| `GET` | `/calls/{id}` | Get call detail (transcript, recording URL) |

### 6. Recording Disclosure

Before the Pipecat pipeline starts processing, play a pre-recorded WAV file: "Esta llamada está siendo grabada para fines de calidad y entrenamiento."

**Implementation:** Generate this audio with Kokoro at agent creation time, store in MinIO, play as first audio frame when call connects (before VAD activates).

### 7. Call Recording

Record both sides of the conversation (agent + caller audio). Options:
- **Option A:** Capture audio frames from the Pipecat pipeline (both input and output) and write to WAV file
- **Option B:** Use Telnyx's built-in recording API

**Phase 1 choice:** Option A (pipeline capture) — no external dependency, full control.

### 8. STT Fallback

If Deepgram WebSocket disconnects or latency exceeds 1 second:
1. Detect failure via timeout or connection error
2. Switch to local `WhisperSTTService` (faster-whisper, large-v3-turbo, already loaded)
3. Log the fallback event
4. Continue processing — caller experiences slower responses but call doesn't drop
5. Monitor Deepgram health, switch back when recovered

---

## Project Structure

```
pipesong/
  src/
    pipesong/
      __init__.py
      config.py              # Environment variables, constants
      main.py                # FastAPI app + WebSocket endpoint
      pipeline.py            # Pipecat pipeline factory
      models/
        __init__.py
        agent.py             # Agent SQLAlchemy model
        call.py              # Call + Transcript models
      api/
        __init__.py
        agents.py            # Agent CRUD endpoints
        calls.py             # Call list/detail endpoints
        telnyx.py            # TeXML webhook endpoint
      services/
        __init__.py
        database.py          # PostgreSQL connection
        storage.py           # MinIO client
        recording.py         # Call recording capture
        disclosure.py        # Pre-recorded disclosure audio
  docker-compose.yml         # PostgreSQL + MinIO
  requirements.txt           # Python dependencies
  .env.example               # Required environment variables
```

---

## Environment Variables

```bash
# Telnyx
TELNYX_API_KEY=your-telnyx-api-key
TELNYX_PHONE_NUMBER=+1234567890

# Deepgram
DEEPGRAM_API_KEY=your-deepgram-api-key

# vLLM (local)
VLLM_BASE_URL=http://localhost:8000/v1

# Kokoro TTS
KOKORO_VOICE=em_alex

# PostgreSQL
DATABASE_URL=postgresql://pipesong:pipesong@localhost:5432/pipesong

# MinIO
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=pipesong-recordings

# App
APP_HOST=0.0.0.0
APP_PORT=8080
DISCLOSURE_TEXT=Esta llamada está siendo grabada para fines de calidad y entrenamiento.
```

---

## Day-by-Day Execution Plan

### Day 1-2: Infrastructure + Accounts

1. Restart TensorDock instance, verify GPU + models still loaded
2. Sign up for Telnyx, buy phone number, create TeXML application
3. Sign up for Deepgram, get API key
4. Create `docker-compose.yml` with PostgreSQL + MinIO
5. Create project structure (`src/pipesong/`)
6. Write `config.py`, `.env.example`
7. Write SQLAlchemy models (`agent.py`, `call.py`)
8. Write `database.py` (async PostgreSQL via asyncpg/SQLAlchemy)
9. Write `storage.py` (MinIO client wrapper)
10. Run migrations, verify DB + MinIO are accessible

### Day 3-4: Pipecat Pipeline

11. Write `pipeline.py` — factory function that creates a Pipecat pipeline given an agent config
12. Integrate: `TelnyxFrameSerializer` + `FastAPIWebsocketTransport`
13. Integrate: `DeepgramSTTService` (Spanish, streaming, interim results)
14. Integrate: `OpenAILLMService` → local vLLM (Qwen 2.5 7B AWQ)
15. Integrate: `KokoroTTSService` (em_alex voice)
16. Integrate: `SileroVADAnalyzer`
17. Test pipeline locally with a simple WebSocket client (before Telnyx)

### Day 5-6: FastAPI + Telnyx Integration

18. Write `main.py` — FastAPI app with WebSocket endpoint at `/ws`
19. Write `telnyx.py` — TeXML webhook that returns `<Stream>` XML
20. Write `agents.py` — CRUD endpoints for agents
21. Write `calls.py` — list/detail endpoints for calls
22. Configure Telnyx TeXML application to point to our webhook URL
23. **First real phone call test** — dial the Telnyx number, hear the AI respond
24. Debug audio issues (sample rate, codec, VAD sensitivity)

### Day 7-8: Recording, Disclosure, Storage

25. Write `disclosure.py` — generate disclosure audio with Kokoro at agent creation
26. Implement: play disclosure audio at call start (before pipeline activates)
27. Write `recording.py` — capture audio frames from pipeline, save to WAV
28. Implement: on call end, upload recording to MinIO, save transcript to PostgreSQL
29. Test full flow: call → disclosure → conversation → hangup → verify DB + MinIO

### Day 9-10: STT Fallback + Polish

30. Implement STT fallback: detect Deepgram failure, switch to local faster-whisper
31. Test fallback by killing Deepgram connection mid-call
32. Add basic error handling: graceful hangup on unrecoverable errors
33. Add logging: call lifecycle events, latency rough estimates
34. End-to-end test: 5 consecutive calls, all complete successfully
35. Document any issues in `docs/phase1-notes.md`

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Telnyx WebSocket format doesn't match Pipecat serializer | Pipecat has `TelnyxFrameSerializer` — well-tested. Check audio encoding matches (PCMU). |
| vLLM on TensorDock crashes again (V1 engine) | Stay on vLLM 0.6.6. Already proven in Phase 0. |
| Kokoro voice sounds different at 8kHz phone quality vs benchmark | Already validated in Phase 0 — em_alex at 8kHz was rated acceptable. |
| Deepgram free tier limits | Deepgram offers $200 free credit — sufficient for Phase 1 testing. |
| TensorDock IP not reachable from Telnyx WebSocket | Need public IP + open port for WebSocket. TensorDock KVM provides this. |
| Audio quality issues (echo, delay, clipping) | Common in first integration. Budget extra time in Day 5-6 for debugging. |

---

## Dependencies to Install

```bash
pip install "pipecat-ai[websocket,deepgram,kokoro,silero]"
pip install fastapi uvicorn
pip install sqlalchemy asyncpg alembic
pip install minio
pip install python-dotenv
```

---

## Success Checklist

- [ ] Telnyx number purchased and TeXML configured
- [ ] Deepgram API key active with Spanish support
- [ ] PostgreSQL running with schema migrated
- [ ] MinIO running with recordings bucket
- [ ] vLLM serving Qwen 2.5 7B AWQ on GPU
- [ ] Pipecat pipeline handles full audio cycle (in → STT → LLM → TTS → out)
- [ ] Disclosure plays at start of every call
- [ ] Transcript saved to PostgreSQL after call ends
- [ ] Recording saved to MinIO after call ends
- [ ] STT fallback activates on Deepgram failure
- [ ] 5 consecutive calls complete successfully
