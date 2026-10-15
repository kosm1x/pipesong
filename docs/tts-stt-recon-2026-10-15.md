# TTS / STT recon, verified against official sources (2026-10-15)

Verification pass over the Jarvis KB note `projects/pipesong/notes/2026-10-15-recon-tts-stt.md`
(a draft built from blog summaries). Every line below was checked on 2026-10-15 against the
Hugging Face model card / API, PyPI, the pipecat `CHANGELOG.md`, or the project's own repo.
Where the note and the source disagree, the source wins and the note's claim is marked WRONG.
Nothing here was listened to or benchmarked; the GPU gates in `ROADMAP-2026-08-27.md` §3 still own that.

## Stack facts (corrections to the note)

| Claim in note | Verified | Source |
| --- | --- | --- |
| Pipecat 1.7.0 in use, PyPI 1.12.0 | Correct. PyPI upload dates: 1.8.0 08-26 · 1.8.1 08-27 · 1.9.0 09-11 · 1.10.0 09-12 · 1.11.0 09-18 · 1.12.0 09-26 (CHANGELOG headers are one day earlier) | PyPI JSON, pipecat CHANGELOG |
| vLLM 0.6.6 in use | Only in `runpod_setup.sh` / roadmap; `requirements.txt` carries no vLLM pin (vLLM runs on the GPU box, not in this venv). Phase 3 target is **0.25.1** (`ROADMAP` §Phase 3), not PyPI's 0.31.0 | `requirements.txt`, `ROADMAP-2026-08-27.md` |
| NeuTTS, Chatterbox, FunASR discarded 03-31 | Correct | `docs/tts-stt-upgrade-research-2026-03-31.md` |

## TTS candidates

| Model | License (official card) | Spanish | Output | Serving path | Verdict for Pipesong |
| --- | --- | --- | --- | --- | --- |
| **NVIDIA Magpie-TTS Multilingual 357M** (`nvidia/magpie_tts_multilingual_357m`) | NVIDIA Open Model License, card says "ready for commercial use" | yes (12 langs) | 22.05 kHz PCM16 mono | NeMo Speech (Python) or NeMo-Speech.cpp locally; NIM container; pipecat has a native `NvidiaTTSService` (Riva gRPC, `server=` points at a self-hosted NIM, default is NVCF cloud) | **First GPU test.** Caveat the note missed: the card lists **5 English speaker voices** (Aria, Jason, Leo, Sofia, John Van Stan) speaking all 12 languages; no native es-MX voice, and zero-shot cloning was removed "for security reasons". Accent in Spanish is the thing to listen for. |
| **Qwen3-TTS** 0.6B / 1.7B (Base, CustomVoice, VoiceDesign) | Apache-2.0 on every weight repo | yes | see 03-31 doc §3 | vLLM-Omni; no pipecat service (`pipecat/services/qwen/` is LLM-only) | Already researched in `tts-stt-upgrade-research-2026-03-31.md` §3; still the cloning option. Integration = custom `TTSService` subclass. |
| **VoxCPM2** (`openbmb/VoxCPM2`) | Apache-2.0 | yes (30 langs per card) | 48 kHz | GGUF/CPU exists | Not evaluated. 48 kHz → 8 kHz wire is wasted compute; low priority. |
| **Kokoro-82M** (current) | Apache-2.0 | yes | 24 kHz | kokoro-onnx via `pipecat-ai[kokoro]`; `Settings.speed` since 1.9.0 | Incumbent. Repo frozen since 08-2025 as the note says. |
| **Supertonic 3** (`Supertone/supertonic-3`) | **OpenRAIL** (card tag `license:openrail`; same family as Supertonic 2 → the use-restriction notes in the 03-31 doc §2 apply) | yes (31 langs) | ONNX ports exist | ONNX | Successor of the model the repo already evaluated; license is use-restricted, not "unverified". |
| **Chatterbox** (`ResembleAI/chatterbox`) | MIT | Multilingual V3 (500M) only | — | — | **Discard stands.** `Chatterbox-Turbo` (350M) and `Chatterbox-Nano` (110M) are **English-only** per the README table; Multilingual V3 README has no streaming API. The note's "check if v3/Turbo solve streaming" is answered: no. |

Not usable commercially, as the note says (not re-verified here): Breeze TTS 2, Fish Audio S2 Pro, Higgs Audio V3, Voxtral TTS.

## STT candidates

| Model | License (official card) | Spanish | Streaming | Serving path | Verdict |
| --- | --- | --- | --- | --- | --- |
| **Nemotron 3.5 ASR Streaming 0.6B** (`nvidia/nemotron-3.5-asr-streaming-0.6b`) | **OpenMDW-1.1** (permissive; the note said "NVIDIA" without naming it) | yes (35 langs) | native, cache-aware FastConformer-RNNT, chunk 80/160/320/560/1120 ms, punctuation + casing built in | NIM container; pipecat native `NvidiaSTTService` (Riva gRPC, maps `Language.ES` → `es-ES`, `ES_US` → `es-US`; no es-MX code) | **Strongest self-hosted Deepgram replacement.** Card publishes WER per language at 1.12 s frames but **no 8 kHz / telephony figure** → the gate stays "measure WER on 8 kHz es-MX call audio". |
| **Voxtral Mini 4B Realtime 2602** (`mistralai/Voxtral-Mini-4B-Realtime-2602`) | Apache-2.0 | yes | yes (~480 ms per the arXiv note, not re-verified) | vLLM | 4B shares the GPU with Qwen2.5-7B-AWQ; second choice. |
| faster-whisper 1.2.1 (note's truncated tail) | MIT | yes | segmented, not streaming | `WhisperSTTService` in pipecat | Not a streaming replacement for Flux; batch/offline only. |

The note's STT list was truncated after two candidates; nothing else in pipecat 1.12's service tree
(`meta`, `moonshine`, `pocket_tts`, `xtts`, `whisper`) is both streaming and Spanish-capable
with a permissive license beyond the two above. `MetaSTTService` (1.9.0) was not checked for license.

## What this changes in the roadmap

- Phase 2 gate (GPU window) is unchanged: Flux 8 kHz live + Spanish WER A/B Flux vs Nova-3.
- A new optional GPU-window item: **Nemotron 3.5 ASR streaming vs Flux on the same 8 kHz es-MX
  clips** (self-hosted NIM, pipecat `NvidiaSTTService`). If it wins on WER and the box has headroom,
  it removes the per-minute Deepgram cost that the Flux price sign-off (§2 item 4) is about.
- TTS: Magpie first listen (accent of English-named voices in Spanish), then Qwen3-TTS per the 03-31 doc.
- Pipecat 1.7.0 → 1.12.0: probed and shipped on the VPS 2026-10-15; results and the two behaviour changes handled are in `REPROBE-2026-08-01.md` §8.
