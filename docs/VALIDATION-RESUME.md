# RESUME HERE — Flux/Pipecat-1.x validation (paused on GPU availability)

**Paused 2026-06-17.** Everything is staged; the only blocker is **no GPU box** (TensorDock had no availability). Pick up here the moment a replacement GPU is up.

> **UPDATE 2026-08-01 — read `docs/REPROBE-2026-08-01.md` FIRST.** Full ecosystem reprobe:
> **C1 is CLOSED by documentation** (Flux quickstart now lists 8 kHz + mulaw as supported), W1
> (Kokoro `language="es"`) closed by research, and the branch has **two install-time breaks**
> (`STTMuteFilter` removed in pipecat 1.0; flux import needs the `.stt` submodule) that must be
> fixed in its Phase 0 — which runs on this VPS, **no GPU needed**. Pin target is now pipecat
> **1.7.0**; vLLM/native-tools modernization is its Phase 3, post-merge.
>
> **Phase 0 COMPLETED 2026-08-04**: the branch now installs and imports clean on 1.7.0
> (4 latent breaks fixed — the two above plus `LLMMessagesFrame`→`LLMRunFrame` and
> `StartInterruptionFrame`→`InterruptionFrame`; nltk ≥3.10 boot guard added in
> `pipesong/__init__.py`; QA warnings closed with `MuteUntilFirstBotCompleteUserMuteStrategy`
>
> - a new `DisclosureGate` processor). **Every remaining merge-gate item is GPU/live-call
>   work** — resume at REPROBE Phase 1 (S1 eot mapping, VPS-only) or Phase 2 (GPU).
>
> **Phase 1 COMPLETED 2026-08-04**: agent turn tuning is now Flux-native (`eot_threshold`/
> `eager_eot_threshold`/`eot_timeout_ms`; `vad_*` columns gone — persistent DBs need
> `scripts/migrations/2026-08-04-agent-eot-columns.sql` applied BEFORE deploying).
> **Only Phase 2 (GPU validation + WER A/B) remains before merge.**
>
> **UPDATE 2026-08-27 — sequence now lives in `docs/ROADMAP-2026-08-27.md`.** The blocker is
> NOT just a GPU: four operator inputs gate Phase 2 (`DEEPGRAM_API_KEY` — no `.env` on the VPS;
> GPU box; Telnyx number liveness; Flux pricing sign-off at $0.0078/min). Meanwhile Phases A.0/A.1
> (pre-recorded disclosure + mixer) and B.1 (MCP over the existing REST) build on the VPS with no
> GPU; their gates (A.2/B.2) run in the same GPU window as Phase 2.

## State

- **Branch:** `flux-pipecat1x` (use the branch HEAD — `git checkout flux-pipecat1x && git pull --ff-only`), pushed to `kosm1x/pipesong`. Flux + Pipecat 1.x **APPLIED, UNVALIDATED, NOT merged.**
- **What changed:** Deepgram Nova-3 → Deepgram **Flux** (`flux-general-multi`, integrated transcription + end-of-turn); Pipecat `0.0.106` → `1.4.0`; Silero VAD + Smart Turn → `ExternalUserTurnStrategies`. Plan + QA gate in `docs/upgrade-flux-pipecat1x-2026-06-17.md`.
- **Blocking gate C1: CLOSED 2026-08-01 (documented-yes).** Deepgram's Flux quickstart explicitly lists raw `sample_rate` 8000 and `mulaw` encoding as supported. `scripts/flux_8k_probe.py` is now an optional live confirmation, not a gate.
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
