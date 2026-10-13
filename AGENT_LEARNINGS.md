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

## 2026-10-13 — A.0 pre-recorded disclosure
- **Avoid:** queuing a plain `OutputAudioRawFrame` for bot speech. Pipecat 1.7's output transport only emits `Bot{Started,Stopped}SpeakingFrame` for `TTSAudioRawFrame`/`SpeechOutputAudioRawFrame` (`transports/base_output.py` `_handle_bot_speech`), so the first-bot-complete unmute and `DisclosureGate` would never fire → read `_handle_bot_speech` before injecting audio.
- **Better:** follow injected `TTSAudioRawFrame` audio with a `TTSStoppedFrame`; without it the bot turn only ends at the transport's 3 s idle fallback (`BOT_VAD_STOP_FALLBACK_SECS`). `tests/test_disclosure_clip.py` pins both behaviours, so a pipecat upgrade that changes them fails loudly.
- **Better:** check what the live service already ships before adding a dependency. Pipecat's `KokoroTTSService` runs on kokoro-onnx (installed via `pipecat-ai[kokoro]`); the roadmap's `kokoro` package would have added 22 packages (spacy et al.) for a second engine that differs from the one callers hear.
- **Mistake:** queued the clip (an output audio frame) at the pipeline head. Every `AudioRawFrame` is STT input (`services/stt_service.py` `process_frame`), so Flux would have transcribed the bot's own disclosure as user speech; QA caught it. Fix: `DisclosureClipPlayer` swaps the disclosure `TTSSpeakFrame` for the clip right before TTS → the check is BOTH a pipeline test with a fake STT recording `run_stt` bytes AND an order assertion on the real `create_pipeline` output (`tests/test_pipeline_order.py`); a hand-built test pipeline does not pin the real order, and a transport-only test catches neither.
- **Mistake:** the first pytest run died at collection: nltk's CWD-import guard blocks pipecat's deps from the in-repo `.venv` unless `pipesong` (which sets `NLTK_DISABLE_IMPORT_SECURITY`) is imported first → `tests/conftest.py` imports `pipesong` before any test module touches pipecat.
