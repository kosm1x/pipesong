# Agent Learnings — pipesong

## Standing rules
- Before paying for a GPU gate on a new STT/TTS model, run the cheapest gate that can kill it: offline CPU WER on `benchmarks/audio/samples/phone_*.wav` bounds streaming WER from above.
- Treat a pass on the 100 synthetic phone clips as necessary, not sufficient. Clean TTS voices flatter every engine, so real es-MX telephony audio decides.
- Normalize numbers (digits vs spelled-out) before scoring Spanish WER. Otherwise formatting dominates the error count.
- Count usted→tú register flips as their own metric. For a formal agent a flip changes meaning; a dropped accent does not.
- Flux does STT, end-of-turn detection and barge-in (`src/pipesong/pipeline.py:90`). Any STT swap must also bring back turn detection (Silero VAD + Smart-Turn), so price that into the integration cost.
- Read a model's license for how it treats OUTPUTS, not just commercial thresholds. NetEase's R2T2 license counts transcripts as derivative works that must not improve another commercial model.

## 2026-09-23 — Confucius4-R2T2 streaming-ASR probe (gate 1)
- **Avoid:** trusting the roadmap's "local faster-whisper fallback" as a live slot. It exists only in `benchmarks/scripts/stt_validation.py` and is not wired into the pipeline → grep `src/pipesong/` for a fallback before calling a candidate a "drop-in".
- **Mistake:** ran the first probe as a session-bound background task and lost it on disconnect → run any job longer than a few minutes under `systemd-run --unit <name>`, resumable (one JSON line per clip), from the start.
- **Avoid:** reading raw WER as recognition quality. 14 of R2T2's 20 wrong clips and 20 of whisper's 23 differed only by number format → normalize numbers, then read the residual errors class by class.
- **Better:** write the kill rule down before running (KILL if more than 1 pt worse than whisper-turbo, or above 10 %), then apply it verbatim. R2T2 scored 4.66 % vs 6.76 %, so it passed. Results: `docs/PROBE-R2T2-2026-09-23.md`.
