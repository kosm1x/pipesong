# RESUME HERE — Flux/Pipecat-1.x validation (paused on GPU availability)

**Paused 2026-06-17.** Everything is staged; the only blocker is **no GPU box** (TensorDock had no availability). Pick up here the moment a replacement GPU is up.

## State

- **Branch:** `flux-pipecat1x` (tip `ce64176`), pushed to `kosm1x/pipesong`. Flux + Pipecat 1.x **APPLIED, UNVALIDATED, NOT merged.**
- **What changed:** Deepgram Nova-3 → Deepgram **Flux** (`flux-general-multi`, integrated transcription + end-of-turn); Pipecat `0.0.106` → `1.4.0`; Silero VAD + Smart Turn → `ExternalUserTurnStrategies`. Plan + QA gate in `docs/upgrade-flux-pipecat1x-2026-06-17.md`.
- **Blocking gate C1:** does Flux accept **8 kHz** telephony? Telnyx sends 8 kHz PCMU; Flux examples use 16 kHz. **Not GPU-dependent** — settle it any time with the probe.
- **Merge-watch:** cloud routine `trig_01XhS4LBYGvTynndAwwkvkx4` (daily 21:11 UTC) flips to MERGED automatically once the branch lands on `main`.

## Validation kit already on the branch

| File                         | Purpose                                                       | Needs GPU? |
| ---------------------------- | ------------------------------------------------------------- | ---------- |
| `docs/validate-flux-8khz.md` | the full step-by-step runbook                                 | —          |
| `scripts/flux_8k_probe.py`   | **C1 test** — streams 8 kHz WAV to Flux, prints accept/reject | **No**     |
| `scripts/runpod_setup.sh`    | turnkey GPU-box bootstrap (clone, venv, vLLM, pipesong)       | Yes        |
| `scripts/resilient.sh`       | run vLLM/pipesong so SSH cut-offs don't kill them             | —          |

## Resume steps (when a GPU box exists)

1. **(Optional, do anytime — no GPU)** settle C1: run `scripts/flux_8k_probe.py` against an 8 kHz Spanish WAV.
   - `SOCKET OPENED` + transcripts → 8 kHz OK.
   - sample-rate rejection → apply runbook **Step 6** (set Flux `sample_rate=16000`, let Pipecat upsample).
2. **Get a GPU** — RunPod (the documented `PLAN.md §5.8` fallback) or any **≥16–24 GB** CUDA box with root + a public port for `:8080`. Qwen2.5‑7B‑AWQ + Kokoro don't need a full 4090.
3. **Bootstrap:** `GH_TOKEN=… DEEPGRAM_API_KEY=… bash scripts/runpod_setup.sh`.
4. **Launch resiliently:**
   - `bash scripts/resilient.sh vllm python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen2.5-7B-Instruct-AWQ --quantization awq --port 8000`
   - `bash scripts/resilient.sh pipesong env PYTHONPATH=src python -m uvicorn pipesong.main:app --host 0.0.0.0 --port 8080`
5. **Run runbook Steps 2–5:** probe → Flux-vs-Nova-3 Spanish WER A/B → live Telnyx call (confirm turn-taking, transcripts, latency) → if all pass, **merge `flux-pipecat1x` → `main`** (the watch then flips MERGED and flags salon-voice-outreach unblocked).

## Don't forget

- **Not on the mission-control VPS** — it has no GPU; vLLM can't run there.
- Re-check the QA gate before merge: `docs/upgrade-flux-pipecat1x-2026-06-17.md` §8 (C1 8 kHz, W1 Kokoro `Language` enum, W4 recording, S1 `vad_*`→`eot_*`).
- Probe query params are unverified-by-running — sanity-check vs the [Flux quickstart](https://developers.deepgram.com/docs/flux/quickstart) before concluding FAIL.
