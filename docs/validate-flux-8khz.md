# Validating Deepgram Flux at 8 kHz — merge-gate C1

**Why this gates the merge:** Telnyx streams **8 kHz PCMU (G.711 µ-law)**; the pipeline decodes that to 8 kHz linear16. Every Deepgram Flux example uses **16 kHz**, and Flux is a separate v2 product — if it rejects 8 kHz, the STT socket never opens and there are no transcripts/turns. This runbook proves (or disproves) 8 kHz support **before** merging `flux-pipecat1x`.

**Where:** the TensorDock GPU box (where pipesong actually runs), not the mission-control VPS.
**Outcome:** PASS → merge the branch (the merge-watch flips to MERGED). FAIL → apply the 16 kHz-resample fallback (Step 6), re-test, then merge.

Run the steps in order; each is cheaper than the next and any failure stops you early.

---

## Step 0 — Prereqs on the GPU box

```bash
# get on the box, then:
cd /path/to/pipesong                # the TensorDock checkout
git fetch origin
git checkout flux-pipecat1x && git pull --ff-only
python3 --version                   # MUST be >= 3.11 (Pipecat 1.0 drops 3.10)
```

- **If Python < 3.11:** that's itself a gate item — make a 3.11+ env first: `uv python install 3.12 && uv venv && source .venv/bin/activate` (or pyenv).
- Install the new deps and watch for resolver errors:

```bash
python -m venv .venv && source .venv/bin/activate   # or reuse pipesong-venv
pip install -r requirements.txt                     # pulls pipecat-ai==1.4.0 + extras
```

- **Import smoke (catches 1.x API drift before any audio):**

```bash
PYTHONPATH=src python -c "import pipesong.pipeline, pipesong.main, pipesong.processors; print('imports OK')"
```

If this throws, fix it first. Most likely suspect from the QA audit: **W1** — `KokoroTTSService.Settings(language="es")` may need the `Language.ES` enum in 1.x. Resolve before continuing.

- Confirm the key is present: `echo "${DEEPGRAM_API_KEY:0:6}…"` (or check `.env`).

---

## Step 1 — Get a representative 8 kHz Spanish sample

Use a **real recorded call** (most representative). Pull one from MinIO (`pipesong-recordings`), or convert any Spanish clip:

```bash
# 8 kHz mono linear16 (what the pipeline feeds Flux)
ffmpeg -i source.wav -ar 8000 -ac 1 -c:a pcm_s16le sample_8k.wav
# 16 kHz version, for the Step 6 fallback comparison
ffmpeg -i sample_8k.wav -ar 16000 -ac 1 -c:a pcm_s16le sample_16k.wav
```

---

## Step 2 — Isolated API test: does Flux accept 8 kHz? (decisive, no phone)

This answers C1 directly, independent of the pipeline. The probe ships in the branch at `scripts/flux_8k_probe.py` (reproduced here for reference — just run it):

```python
import asyncio, json, os, sys, wave
import websockets  # pip install websockets

API_KEY = os.environ["DEEPGRAM_API_KEY"]
WAV = sys.argv[1]

# NOTE: verify these query params against the live Flux quickstart:
# https://developers.deepgram.com/docs/flux/quickstart  and  /docs/flux/configuration
URL = ("wss://api.deepgram.com/v2/listen"
       "?model=flux-general-multi"
       "&encoding=linear16"
       "&sample_rate=8000"
       "&eot_threshold=0.7")

async def main():
    wf = wave.open(WAV, "rb")
    assert wf.getframerate() == 8000 and wf.getsampwidth() == 2 and wf.getnchannels() == 1, \
        "sample must be 8 kHz mono PCM16"
    # websockets >=12 uses additional_headers; older uses extra_headers
    async with websockets.connect(URL, additional_headers={"Authorization": f"Token {API_KEY}"}) as ws:
        print(">>> SOCKET OPENED at 8 kHz")   # if you never see this, 8 kHz was rejected at connect
        async def send():
            while (chunk := wf.readframes(1600)):  # 0.2 s @ 8 kHz
                await ws.send(chunk); await asyncio.sleep(0.18)
            await ws.send(json.dumps({"type": "CloseStream"}))
        async def recv():
            async for msg in ws:
                print("RECV:", str(msg)[:600])
        await asyncio.gather(send(), recv())

asyncio.run(main())
```

```bash
python scripts/flux_8k_probe.py sample_8k.wav
```

**Read the output:**

- ✅ **PASS** — you see `SOCKET OPENED` and `RECV:` messages containing Spanish transcript / turn (EndOfTurn) events.
- ❌ **FAIL** — connect raises, or the socket closes immediately with an error mentioning sample rate / unsupported / 400. → go to Step 6 (fallback).
- ⚠️ If params are wrong (not a sample-rate issue), cross-check names against the Flux docs linked in the script and retry. Don't conclude FAIL until the only variable left is the 8 kHz rate.

---

## Step 3 — Transcription quality at 8 kHz (Flux-multi vs current Nova-3-es)

Acceptance isn't just "socket opens" — Flux's Spanish at 8 kHz must be at least as good as Nova-3. Run 5–10 real recorded Spanish calls through both and compare:

- Flux: the probe above (or the pipeline) → collect transcripts.
- Nova-3 (current prod): same audio, `model=nova-3`, `language=es`, 8 kHz.
- Eyeball side-by-side; if you have ground-truth transcripts, compute rough WER.

**Go/No-Go:** Flux ≈ or better than Nova-3 on Mexican-Spanish telephony → proceed. Noticeably worse → do not merge; keep Nova-3 (Flux's turn-taking isn't worth a transcription regression).

---

## Step 4 — Live end-to-end call

Run the branch as production would:

```bash
PYTHONPATH=src python -m uvicorn pipesong.main:app --host 0.0.0.0 --port 8080
# ensure the Telnyx TeXML webhook points at this box, then call +12678840093 in Spanish
```

Watch the logs for:

- ✅ no Deepgram/STT socket error (the C1 failure would surface here too)
- ✅ turn-taking feels natural (Flux EOT driving turns; Silero/Smart-Turn are gone)
- ✅ transcripts land in PostgreSQL; recording in MinIO
- ✅ the expected warning `Agent vad_stop_secs/vad_confidence are IGNORED under Deepgram Flux` (confirms the new turn path is active)
- check per-turn latency via `GET /calls/{id}/latency` — Flux EOT should not blow the budget

---

## Step 5 — Decision

- **All pass →** merge `flux-pipecat1x` into `main` (PR or fast-forward). The cloud merge-watch (`trig_01XhS4LBYGvTynndAwwkvkx4`) flips to MERGED next run and flags salon-voice-outreach unblocked. Record results in `docs/PROGRESS.md`, tick the gate in `docs/upgrade-flux-pipecat1x-2026-06-17.md` §6/§8, and update README's stack table (now it's true).
- **Quality fail →** abandon/park the branch; stay on Nova-3.

---

## Step 6 — Fallback: 8 kHz rejected → upsample to 16 kHz

If Step 2 fails purely on sample rate, feed Flux 16 kHz and let Pipecat resample the 8 kHz input up:

1. In `src/pipesong/pipeline.py`, set the Flux service to `sample_rate=16000` (Pipecat resamples the 8 kHz transport audio to 16 kHz before STT). Keep `flux_encoding="linear16"`.
2. Re-run Step 2 with `sample_16k.wav` to confirm 16 kHz works (it should — it's the documented rate).
3. Re-run Step 4 (live call) — the transport still ingests 8 kHz from Telnyx; only the STT leg runs at 16 kHz.
4. Re-check latency: upsampling adds a small amount; confirm it stays within budget.
5. If 16 kHz passes, commit the `sample_rate=16000` change to the branch and proceed to Step 5.

---

## Quick reference

| Check                     | Command / signal                                | Pass                          |
| ------------------------- | ----------------------------------------------- | ----------------------------- |
| Python                    | `python3 --version`                             | ≥ 3.11                        |
| Deps + imports            | `pip install -r requirements.txt`; import smoke | no errors                     |
| **C1: Flux @ 8 kHz**      | `python scripts/flux_8k_probe.py sample_8k.wav` | `SOCKET OPENED` + transcripts |
| Quality                   | Flux vs Nova-3 on real Spanish                  | Flux ≥ Nova-3                 |
| Live call                 | uvicorn + real call                             | no STT error, turns work      |
| Fallback (if 8 kHz fails) | Flux `sample_rate=16000`                        | 16 kHz socket opens           |
