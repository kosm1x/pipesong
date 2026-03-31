# TTS/STT Upgrade Research -- Awesome AI Voice Evaluation

**Date**: 2026-03-31
**Context**: Pipesong voice AI engine evaluation of open-source models from [wildminder/awesome-ai-voice](https://github.com/wildminder/awesome-ai-voice) for potential upgrades to the current stack.
**Current stack**: Kokoro-82M TTS (389--554ms TTFB), Deepgram Nova-3 STT (cloud, 234--269ms), Qwen 2.5 7B AWQ LLM, Telnyx SIP (8kHz G.711 mulaw), targeting Spanish/LATAM market.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Supertonic 2 -- TTS Replacement](#2-supertonic-2)
3. [Qwen3-TTS -- TTS Replacement](#3-qwen3-tts)
4. [NeuTTS -- CPU-Offloaded TTS](#4-neutts)
5. [KokoClone -- Voice Cloning Layer](#5-kokoclone)
6. [Fun-CosyVoice 3.0 -- TTS Replacement](#6-fun-cosyvoice-30)
7. [Chatterbox -- TTS Replacement](#7-chatterbox)
8. [FunASR -- STT Replacement](#8-funasr)
9. [LavaSR -- Audio Enhancement](#9-lavasr)
10. [Comparison Matrix](#10-comparison-matrix)
11. [Recommended Evaluation Plan](#11-recommended-evaluation-plan)

---

## 1. Executive Summary

### What to test first (ranked by expected impact)

| Priority | Model                 | Role          | Expected Impact                                                                                          | Effort                                                           |
| -------- | --------------------- | ------------- | -------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| **1**    | **Supertonic 2**      | TTS           | Drop TTS from ~450ms to <30ms on CPU. Biggest single latency win.                                        | Low -- pip install, write Pipecat adapter (~50 LOC)              |
| **2**    | **Qwen3-TTS 0.6B**    | TTS           | 97ms streaming TTFB, voice cloning, voice design from text. Natural Qwen ecosystem fit.                  | Medium -- needs custom server wrapper, VRAM sharing with LLM     |
| **3**    | **Fun-CosyVoice 3.0** | TTS           | Production-grade streaming with FastAPI/gRPC server included. Spanish is 1 of 9 core languages.          | Medium -- 11.7 GB download, 4-6 GB VRAM                          |
| **4**    | **LavaSR**            | Audio enhance | 8kHz->48kHz + denoising at 5000x realtime. Near-zero latency. Could improve STT accuracy on noisy calls. | Low -- pip install, ~20 LOC inline processor                     |
| **5**    | **KokoClone**         | Voice cloning | Add custom Mexican voice to existing Kokoro. No TTS swap needed.                                         | Medium -- adds 200-500ms latency per utterance (post-processing) |

### What to skip

| Model          | Why                                                                                                                                                      |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **NeuTTS**     | Spanish model is Nano-only with $5M revenue-cap license. Quality on Mexican Spanish is unknown. Codec decoder alone is 3.5 GB. Not worth the complexity. |
| **Chatterbox** | Spanish requires 500M Multilingual model with no streaming support. Mandatory watermark on all output. No server included.                               |
| **FunASR**     | Spanish is not supported in any streaming model. The only Spanish path is offline Whisper. Dead end.                                                     |

---

## 2. Supertonic 2

**Verdict: HIGH PRIORITY -- test immediately. Potential to eliminate TTS as a latency bottleneck entirely.**

### Key Facts

| Attribute          | Value                                                       |
| ------------------ | ----------------------------------------------------------- |
| Developer          | Supertone Inc. (South Korea)                                |
| Released           | 2026-01-06                                                  |
| Parameters         | 66M                                                         |
| Architecture       | Flow-matching text-to-latent + speech autoencoder + vocoder |
| Model size on disk | ~263 MB (ONNX)                                              |
| Output sample rate | 44,100 Hz                                                   |
| Languages          | English, Korean, **Spanish**, Portuguese, French            |
| License (code)     | MIT                                                         |
| License (model)    | OpenRAIL-M (commercial OK, with use-based restrictions)     |
| Install            | `pip install supertonic`                                    |

### Performance

RTF benchmarks (2 inference steps):

| Platform           | Short (59 chars) | Mid (152 chars) | Long (266 chars) |
| ------------------ | ---------------- | --------------- | ---------------- |
| RTX 4090 (PyTorch) | 0.005            | 0.002           | 0.001            |
| M4 Pro CPU (ONNX)  | 0.015            | 0.013           | 0.012            |

For a typical 150-char Spanish sentence on CPU ONNX: **~15-25ms total synthesis time**. This is so fast that batch mode is effectively indistinguishable from streaming -- the entire sentence renders before the first audio chunk would even need to start playing.

Compared to Kokoro on M4 Pro CPU ONNX (RTF 0.124-0.144): **Supertonic is 8-10x faster**.

### Spanish Support

- Spanish is a first-class language (code: `es`, max chunk: 300 chars)
- No distinction between Castilian and Mexican Spanish
- Character-level input (no language-specific G2P) -- handles Unicode natively
- Text normalized via NFKD; diacritics handling needs verification for `n` with tilde
- No published per-language MOS scores or quality benchmarks

### Voice Options

- 10 preset voices: M1-M5 (male), F1-F5 (female)
- No zero-shot voice cloning from audio
- **Voice Builder** (commercial service at supertonic.supertone.ai) can generate custom voice style JSONs from recordings
- Community tool for voice interpolation/mixing exists

### Integration with Pipecat

No existing integration. Custom service needed:

```python
from supertonic import TTS
import numpy as np
import asyncio

class SupertonicTTSService(TTSService):
    def __init__(self):
        self.tts = TTS(auto_download=True)
        self.style = self.tts.get_voice_style("F1")  # pick a female voice

    async def run_tts(self, text: str) -> AsyncGenerator[bytes, None]:
        # ONNX inference is synchronous -- run in thread pool
        wav, dur = await asyncio.to_thread(
            self.tts.synthesize,
            text, voice_style=self.style, lang="es", total_steps=2, speed=1.05
        )
        # Convert float32 to int16 PCM
        pcm = (wav.squeeze() * 32767).astype(np.int16).tobytes()
        yield pcm
```

Key detail: ONNX runtime only supports CPU (`CPUExecutionProvider`). GPU ONNX raises `NotImplementedError`. The PyTorch model (which enables GPU) is **not publicly released** -- only the ONNX weights are on HuggingFace.

### Risks and Gotchas

1. **GPU ONNX not supported** -- headline RTX 4090 numbers (RTF 0.001) are from unreleased PyTorch model. CPU-only ONNX is still very fast (RTF ~0.012) but not the marketing numbers.
2. **No streaming architecture** -- flow-matching requires full duration prediction upfront. Mitigated by the fact that synthesis is so fast it doesn't matter.
3. **numpy < 2.0 required** -- could conflict with other dependencies.
4. **NFKD normalization strips diacritics** -- test with `n` carefully.
5. **No SSML support** -- no pronunciation hints for Mexican idioms.
6. **No emotion/prosody control** beyond speed (0.7x-2.0x).
7. **Young project** (4 months old) -- limited production track record.

### OpenRAIL-M License Notes for Voice AI

- Commercial use: **YES**
- Must inform callers they're speaking to AI (restriction e: disclose machine-generated content)
- Cannot impersonate a specific person without consent (restriction g)
- Must pass same use-based restrictions downstream

### Evaluation Checklist

- [ ] `pip install supertonic` on TensorDock GPU server
- [ ] Synthesize 20 Spanish sentences covering: greetings, numbers, dates, addresses, questions, Mexican slang
- [ ] Verify `n` (n with tilde) pronunciation in words like "manana", "senor", "espanol"
- [ ] Measure end-to-end synthesis time on server CPU for sentence-length inputs
- [ ] Test all 10 voices for Spanish naturalness (subjective listening)
- [ ] Build Pipecat adapter prototype, measure pipeline TTFB
- [ ] Compare audio quality vs Kokoro em_alex on same sentences (A/B listening test)
- [ ] Verify numpy < 2.0 compatibility with rest of pipesong dependencies

---

## 3. Qwen3-TTS

**Verdict: HIGH PRIORITY -- the most feature-complete option. Voice cloning + streaming + Spanish + text-based voice design. Best fit if VRAM budget allows.**

### Key Facts

| Attribute      | Value                                                                                         |
| -------------- | --------------------------------------------------------------------------------------------- |
| Developer      | Alibaba Cloud / Qwen Team                                                                     |
| Released       | 2026-01-22                                                                                    |
| Parameters     | 0.6B (Base/CustomVoice) or 1.7B (all variants)                                                |
| Architecture   | LLM backbone + 12Hz speech codec + causal ConvNet decoder                                     |
| Languages (10) | Chinese, English, Japanese, Korean, German, French, Russian, Portuguese, **Spanish**, Italian |
| License        | Apache 2.0                                                                                    |
| Install        | `pip install -U qwen-tts`                                                                     |
| Paper          | [arXiv:2601.15621](https://arxiv.org/abs/2601.15621)                                          |

### Model Variants

| Model                             | Params | VRAM (est.) | Voice Clone     | Voice Design | Instruct Control |
| --------------------------------- | ------ | ----------- | --------------- | ------------ | ---------------- |
| `Qwen3-TTS-12Hz-0.6B-Base`        | 0.6B   | ~3-4 GB     | Yes (3s ref)    | No           | No               |
| `Qwen3-TTS-12Hz-0.6B-CustomVoice` | 0.6B   | ~3-4 GB     | No (9 presets)  | No           | No               |
| `Qwen3-TTS-12Hz-1.7B-Base`        | 1.7B   | ~6-8 GB     | Yes (3s ref)    | No           | No               |
| `Qwen3-TTS-12Hz-1.7B-CustomVoice` | 1.7B   | ~6-8 GB     | Yes (9 presets) | No           | Yes              |
| `Qwen3-TTS-12Hz-1.7B-VoiceDesign` | 1.7B   | ~6-8 GB     | No              | **Yes**      | Yes              |

### Streaming Latency

Measured with `torch.compile` + CUDA Graph (GPU not specified in paper -- likely A100/H100):

| Model        | First-Packet Latency | Time-Per-Packet | RTF   | Concurrent |
| ------------ | -------------------- | --------------- | ----- | ---------- |
| 1.7B (1 req) | **101ms**            | 21ms            | 0.313 | 1          |
| 0.6B (1 req) | **97ms**             | --              | 0.288 | 1          |
| 1.7B (6 req) | **333ms**            | --              | 0.463 | 6          |

The 97ms is achieved because: 12Hz codec = only 12.5 frames/sec, dual-track architecture (backbone predicts semantic codebook, MTP module generates 15 residual codebooks in parallel), causal ConvNet decoder starts from first frame.

**On your TensorDock GPU**: expect higher latency than paper numbers. The 97ms assumes high-end datacenter GPU with torch.compile + CUDA Graph. Realistic estimate: 150-300ms first packet on an A5000/RTX 4090 equivalent.

### Voice Cloning (Killer Feature)

```python
from qwen_tts import Qwen3TTSModel

model = Qwen3TTSModel.from_pretrained(
    "Qwen/Qwen3-TTS-12Hz-0.6B-Base", device_map="cuda:0", dtype=torch.bfloat16
)

# Pre-compute voice profile (do once, reuse forever)
prompt = model.create_voice_clone_prompt(
    ref_audio="mexican_speaker_10s.wav",
    ref_text="Transcript of the reference audio."
)

# Generate with cloned voice
wavs, sr = model.generate_voice_clone(
    text="Buenos dias, en que le puedo ayudar?",
    language="Spanish",
    voice_clone_prompt=prompt  # reusable!
)
```

Modes:

- **In-Context Learning (ICL)**: ref_audio + ref_text -> best quality
- **X-vector only**: ref_audio only, no transcript needed, lower quality

### Voice Design from Text (1.7B VoiceDesign only)

Create voices from natural language descriptions:

```python
design_model = Qwen3TTSModel.from_pretrained(
    "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign", ...
)
ref_wavs, sr = design_model.generate_voice_design(
    text="Hola, bienvenido a nuestra empresa.",
    language="Spanish",
    instruct="Female, 28 years old, warm Mexican accent, professional but friendly tone"
)
# Save this as reference audio, then use with Base model for production
```

Workflow: design a voice with VoiceDesign -> save output as reference clip -> use with Base model's voice clone for production calls.

### Spanish Quality Benchmarks

From the paper's multilingual evaluation:

| Metric                              | Qwen3-TTS 1.7B | Qwen3-TTS 0.6B | MiniMax | ElevenLabs |
| ----------------------------------- | -------------- | -------------- | ------- | ---------- |
| Spanish WER (lower=better)          | **1.126**      | 1.491          | 1.029   | 1.084      |
| Spanish Speaker SIM (higher=better) | **0.814**      | 0.812          | 0.762   | 0.615      |

Qwen3-TTS has the best speaker similarity for Spanish among all tested models. WER is competitive with ElevenLabs.

### VRAM Coexistence with Qwen 2.5 7B LLM

On a 24 GB GPU:

- Qwen 2.5 7B AWQ (4-bit): ~8 GB via vLLM
- Qwen3-TTS 0.6B (bf16): ~3-4 GB via qwen-tts
- **Total: ~11-12 GB** -- fits comfortably with headroom

The two models run as **separate processes**. Qwen3-TTS does NOT run on vLLM (requires vLLM-Omni fork, which is offline-only). Use the `qwen-tts` Python package directly.

### Integration Path

No HTTP API included. Options:

1. **Wrap in FastAPI server** with WebSocket streaming (recommended)
2. **Direct Python integration** in Pipecat pipeline (simpler but tighter coupling)
3. **vLLM-Omni** (not ready -- online serving not yet available)

The `qwen-tts` package `generate_*` methods return complete waveforms (no streaming). For streaming, you'd need vLLM-Omni's `AsyncOmni` which yields chunks -- but this is offline-only currently.

**Practical streaming approach**: Use sentence-level batching (same as current Kokoro setup). Feed each sentence to `generate_voice_clone()`, return complete audio per sentence. At 101ms first-packet + RTF 0.3, a 5-second sentence renders in ~1.6 seconds total. First sentence would start playing ~100ms after LLM finishes the first sentence.

### Risks and Gotchas

1. **No production HTTP server** -- must build custom serving layer
2. **97ms latency is on unknown (likely A100+) hardware** -- your GPU will be slower
3. **0.6B lacks VoiceDesign** -- need 1.7B for text-to-voice, which doubles VRAM
4. **No Mexican Spanish preset** -- must use voice cloning with Mexican reference clip
5. **Dependencies are heavy**: transformers 4.57.3, accelerate 1.12.0, torchaudio, sox
6. **Fine-tuning supported** but requires dataset preparation pipeline

### Evaluation Checklist

- [ ] Install `qwen-tts` on TensorDock server
- [ ] Measure actual first-packet latency with 0.6B Base on your GPU
- [ ] Record or source a 10s Mexican Spanish reference audio clip
- [ ] Test voice cloning quality with Mexican reference
- [ ] Test 1.7B VoiceDesign with Mexican accent description
- [ ] Measure VRAM usage alongside running vLLM (Qwen 2.5 7B AWQ)
- [ ] Build FastAPI WebSocket wrapper, measure end-to-end latency in Pipecat
- [ ] Compare voice clone quality vs Kokoro em_alex (subjective A/B test)
- [ ] Stress test: 5 concurrent TTS requests while LLM is serving

---

## 4. NeuTTS

**Verdict: SKIP. The Apache-2.0 model (Air) is English-only. Spanish is Nano-only with a $5M revenue-cap license. Codec decoder is 3.5 GB. Not worth the complexity vs alternatives.**

### Why Not

| Issue                 | Detail                                                                                                    |
| --------------------- | --------------------------------------------------------------------------------------------------------- |
| License               | Spanish model (Nano) uses "NeuTTS Open License v1.0" -- free under $5M annual revenue, paid license above |
| Spanish quality       | Generic `es` via espeak-ng (Castilian phonemization). No Mexican Spanish data or testing.                 |
| Codec overhead        | NeuCodec ONNX decoder is 3.5 GB on disk. Published benchmarks exclude codec decode time.                  |
| Context limit         | 2048 tokens = ~30s audio including reference. Long utterances need app-level chunking.                    |
| espeak-ng sensitivity | Old versions cause "significant phonemisation issues for non-English languages"                           |

### If Revisited Later

The NeuTTS-Air architecture (Qwen2 0.5B backbone, GGUF, CPU inference at 2-4x realtime) is sound. If they release a multilingual Air model under Apache 2.0, it would be worth reevaluating. The streaming support (via llama-cpp-python, 500ms chunks) works but has uncharacterized TTFA.

---

## 5. KokoClone

**Verdict: MEDIUM PRIORITY -- only if custom Mexican voice is a near-term requirement AND you're keeping Kokoro as the TTS engine. The 200-500ms latency penalty per utterance is the main concern.**

### Key Facts

| Attribute     | Value                                                                   |
| ------------- | ----------------------------------------------------------------------- |
| Architecture  | Kokoro-82M (TTS) + Kanade 120M (voice conversion) -- two-phase pipeline |
| Languages     | 8: en, hi, fr, ja, zh, it, **es**, pt                                   |
| Voice cloning | Zero-shot, 3-10s reference audio                                        |
| License       | Apache 2.0 (KokoClone + Kokoro), MIT (Kanade)                           |
| GitHub        | 72 stars, created 2026-03-03                                            |

### How It Works

1. **Phase 1**: Kokoro synthesizes text using a donor voice (`im_nicola` for Spanish -- an Italian male voice)
2. **Phase 2**: Kanade voice converter extracts content tokens from Kokoro output + speaker embedding from reference audio -> generates new audio with reference voice's timbre

This is post-processing voice conversion, not modified TTS.

### Critical Limitations

1. **Breaks streaming**: Kanade needs the COMPLETE Kokoro output before conversion. Your current sentence-streaming pipeline would need to buffer each full sentence, convert it, then play -- adding 200-500ms per utterance.
2. **Double vocoding**: Kokoro generates audio -> Kanade re-encodes to mel -> re-vocodes. Each pass introduces artifacts.
3. **Kanade trained on English only** (LibriTTS). Voice conversion quality on Mexican Spanish is unvalidated.
4. **Spanish donor voice is Italian** (`im_nicola`). Italian prosodic patterns may bleed through.
5. **Additional VRAM**: ~1.5 GB on top of Kokoro for Kanade + Vocos vocoder.

### Pre-cached Voice Profiles

Partially possible with custom code:

```python
# Extract speaker embedding once
features = kanade.encode(reference_waveform)
cached_embedding = features.global_embedding  # save this

# Reuse per-call (saves ~50-100ms reference encoding, NOT the main conversion)
mel = kanade.decode(
    content_token_indices=source_features.content_token_indices,
    global_embedding=cached_embedding
)
```

KokoClone doesn't expose this API directly -- requires calling Kanade's internals.

### When KokoClone Makes Sense

- You need a custom Mexican voice **now** and Kokoro is staying as your TTS engine
- The 200-500ms latency penalty is acceptable (current p50 is 830ms, would push to ~1100-1300ms)
- You can validate that Kanade's English-trained conversion works acceptably on Spanish

### When to Skip KokoClone

- If you switch TTS to Qwen3-TTS (which has built-in voice cloning with 3s reference, no post-processing)
- If you switch to Supertonic + Voice Builder (commercial service for custom voices)
- If latency budget is tight and the 200-500ms hit is unacceptable

---

## 6. Fun-CosyVoice 3.0

**Verdict: STRONG ALTERNATIVE -- the most production-ready option with built-in servers (FastAPI, gRPC, Docker, TRT-LLM). Spanish is a core language. Streaming is native. Main concern is real-world first-chunk latency.**

### Key Facts

| Attribute          | Value                                                          |
| ------------------ | -------------------------------------------------------------- |
| Developer          | Alibaba FunAudioLLM                                            |
| Parameters         | 0.5B (LLM) + flow matching + vocoder                           |
| Languages (9)      | zh, en, ja, ko, de, **es**, fr, it, ru (+ 18 Chinese dialects) |
| Training data      | ~1M hours                                                      |
| License            | Apache 2.0                                                     |
| Model size on disk | 11.77 GB                                                       |
| VRAM (est.)        | 4-6 GB                                                         |
| GitHub             | 20,315 stars                                                   |

### Streaming

Native bi-streaming: text-in streaming (generator) + audio-out streaming (yields chunks).

```python
def text_generator():
    yield "Buenos dias, "
    yield "en que le puedo ayudar hoy?"

for i, chunk in enumerate(cosyvoice.inference_zero_shot(
    text_generator(), prompt_text, prompt_wav, stream=True
)):
    # chunk['tts_speech'] is a tensor, play immediately
    play_audio(chunk['tts_speech'])
```

### The 150ms Latency Claim -- Reality Check

The "150ms first packet" comes from CosyVoice 2.0 marketing. Real benchmarks tell a different story:

| Setup                              | First Chunk Latency      | RTF       |
| ---------------------------------- | ------------------------ | --------- |
| Marketing claim                    | 150ms                    | --        |
| **L20 GPU, TRT-LLM, 4 concurrent** | **750ms avg, 941ms P90** | 0.05-0.11 |

The 150ms is likely achievable only with: very short text, A100/H100 GPU, full TensorRT optimization, single-request scenario.

**For pipesong on TensorDock**: expect 500-1000ms first-chunk latency depending on GPU and optimization level. This is comparable to or worse than current Kokoro TTFB (389-554ms).

### Voice Cloning

- Zero-shot: reference audio + transcription
- Cross-lingual: clone a voice and synthesize in a different language
- Speaker embedding save/load: `add_zero_shot_spk()` / `save_spkinfo()` for reuse
- Speaker similarity: 78.0% (zh), 71.8% (en) -- surpasses human baseline

### Quality Benchmarks

| Model                 | test-zh CER% | test-en WER% | test-hard CER% |
| --------------------- | ------------ | ------------ | -------------- |
| **Fun-CosyVoice3 RL** | **0.81**     | **1.68**     | **5.44**       |
| Fun-CosyVoice3        | 1.21         | 2.24         | 6.71           |
| CosyVoice2            | 1.45         | 2.57         | 6.83           |
| Seed-TTS (closed)     | 1.12         | 2.25         | 7.59           |

No Spanish-specific benchmarks published.

### Production Deployment (Key Differentiator)

Unlike Supertonic and Qwen3-TTS, CosyVoice ships with production infrastructure:

- **FastAPI server**: `StreamingResponse` endpoints for all inference modes
- **gRPC server**: Protobuf-based streaming with configurable concurrency
- **Docker**: Pre-built containers
- **TensorRT-LLM + Triton**: NVIDIA-contributed production deployment with docker-compose
- **vLLM**: Supports vLLM 0.9.0+ for LLM component acceleration

### Risks and Gotchas

1. **Real first-chunk latency is 750ms+** on production hardware, not 150ms
2. **11.77 GB download** -- large model footprint
3. **4-6 GB VRAM** -- competes with LLM for GPU memory
4. **No Spanish-specific benchmarks** -- quality unknown for Mexican Spanish
5. **Complex dependency tree** -- multiple model components (LLM, flow, vocoder, tokenizer, speaker verifier)

### Evaluation Checklist

- [ ] Docker deploy on TensorDock server
- [ ] Measure actual first-chunk streaming latency for Spanish text
- [ ] Test zero-shot voice cloning with Mexican Spanish reference
- [ ] Test cross-lingual cloning (English reference -> Spanish output)
- [ ] Measure VRAM usage, verify coexistence with vLLM
- [ ] Compare audio quality vs Kokoro and Supertonic on same Spanish sentences
- [ ] Stress test: concurrent requests via gRPC server

---

## 7. Chatterbox

**Verdict: SKIP. Spanish requires the 500M Multilingual model which has no streaming. Mandatory watermark on all output. No server infrastructure included.**

### Why Not

| Issue      | Detail                                                                                                                |
| ---------- | --------------------------------------------------------------------------------------------------------------------- |
| Streaming  | None. Batch generation only. "Sub 200ms" refers to Resemble AI's commercial cloud service, not the open-source model. |
| Spanish    | Multilingual model only (500M). Turbo (350M, the fast one) is English-only.                                           |
| Watermark  | Perth watermarker is embedded in `generate()` and cannot be disabled without forking.                                 |
| No server  | Python library only. No FastAPI, no gRPC, no Docker.                                                                  |
| Benchmarks | Subjective only (Podonos platform), English only. No WER/CER for any language.                                        |

### If Revisited

The Turbo model's 1-step distilled flow matching is interesting architecturally. If they release a multilingual Turbo model with streaming support, it would be worth reevaluating.

---

## 8. FunASR

**Verdict: DEAD END for Spanish. Not a single streaming-capable FunASR model supports Spanish.**

### Why Not

| Issue             | Detail                                                                                                                     |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Spanish streaming | Not supported. Fun-ASR-MLT-Nano covers 31 languages -- Spanish is explicitly absent (Portuguese is there, Spanish is not). |
| Whisper fallback  | Whisper-large-v3 is available through FunASR but is offline-only (no streaming).                                           |
| Streaming latency | Even if Spanish were supported, streaming chunk latency is 480-600ms -- worse than Deepgram (234-269ms).                   |

### Better Alternatives for Replacing Deepgram

If self-hosted Spanish streaming ASR is a future goal, investigate instead:

| Option                                 | Model                  | Params  | VRAM           | Spanish            | Streaming     |
| -------------------------------------- | ---------------------- | ------- | -------------- | ------------------ | ------------- |
| **faster-whisper + whisper-streaming** | Whisper-large-v3-turbo | 809M    | ~2-3 GB (INT8) | Native             | Yes (chunked) |
| **NVIDIA Canary-1B**                   | Canary                 | 1B      | ~3-4 GB        | Native (6.67% WER) | Yes           |
| **NVIDIA Riva/Parakeet**               | Various                | Various | Various        | Yes                | Yes (NIM)     |

These are not from the awesome-ai-voice list but are the actual candidates if Deepgram replacement becomes a priority.

---

## 9. LavaSR

**Verdict: LOW-EFFORT EXPERIMENT -- could improve STT accuracy on noisy phone calls. Near-zero latency and VRAM impact. Worth testing.**

NovaSR (from the awesome-ai-voice list) is superseded by the same author's **LavaSR**. NovaSR only accepts 16kHz input; LavaSR handles 8kHz natively.

### Key Facts

| Attribute                   | NovaSR (skip)      | LavaSR (use this)      |
| --------------------------- | ------------------ | ---------------------- |
| Input sample rates          | 16kHz only         | **8-48kHz (any)**      |
| Output sample rate          | 48kHz              | 48kHz                  |
| Model size                  | 52KB               | 50MB                   |
| Speed (A100 GPU)            | 3600x realtime     | **5000x realtime**     |
| Speed (CPU)                 | unknown            | 50-80x realtime        |
| VRAM                        | negligible         | 500MB                  |
| Quality (LSD, lower=better) | no benchmarks      | **0.85** (8kHz->48kHz) |
| Denoising                   | No                 | **Yes**                |
| License                     | Apache 2.0         | Apache 2.0             |
| GitHub                      | ysharma3501/LavaSR | ysharma3501/LavaSR     |

### Integration Point

Pre-STT enhancement (improve ASR accuracy on noisy phone audio):

```
8kHz G.711 mulaw -> decode to PCM -> LavaSR (8kHz->48kHz + denoise) -> resample to 16kHz -> Deepgram STT
```

```python
from LavaSR.model import LavaEnhance2

enhancer = LavaEnhance2("YatharthS/LavaSR", device="cuda:0")
# In the Pipecat pipeline, before STT:
enhanced = enhancer.enhance(phone_audio_8khz)  # 8kHz -> 48kHz + denoised
# Resample to 16kHz for Deepgram
```

Latency impact: <0.2ms per second of audio at 5000x realtime. Effectively zero.

### Caveats

- Bandwidth extension "hallucinates" high-frequency content that wasn't in the 8kHz signal
- Deepgram Nova-3 is already optimized for narrowband phone audio -- feeding it synthetic wideband may help or hurt
- The denoising feature could be the real value for noisy call environments
- No published benchmarks on how audio enhancement affects downstream ASR accuracy

### Evaluation Checklist

- [ ] Install LavaSR, test on sample 8kHz phone recordings
- [ ] A/B test: Deepgram accuracy on raw 8kHz vs LavaSR-enhanced audio
- [ ] Test specifically with background noise samples (street, office, car)
- [ ] Measure actual latency in pipeline

---

## 10. Comparison Matrix

### TTS Candidates Head-to-Head

|                         | Supertonic 2                     | Qwen3-TTS 0.6B          | CosyVoice 3.0                   | Kokoro (current)      |
| ----------------------- | -------------------------------- | ----------------------- | ------------------------------- | --------------------- |
| **Spanish**             | First-class                      | 1 of 10 langs           | 1 of 9 langs                    | Via multilingual      |
| **Mexican Spanish**     | Unknown quality                  | Clone from ref audio    | Clone from ref audio            | em_alex (placeholder) |
| **Streaming**           | No (but irrelevant at RTF 0.001) | Yes (101ms TTFP)        | Yes (150ms claimed, 750ms real) | Yes (389-554ms TTFB)  |
| **Voice Cloning**       | No (Voice Builder commercial)    | Yes (3s ref, zero-shot) | Yes (zero-shot, cross-lingual)  | No                    |
| **Model Size**          | 263 MB                           | ~900 MB                 | 11.77 GB                        | ~82 MB                |
| **VRAM**                | 0 (CPU only)                     | 3-4 GB                  | 4-6 GB                          | ~500 MB               |
| **Params**              | 66M                              | 600M                    | 500M+                           | 82M                   |
| **RTF**                 | 0.012 CPU / 0.001 GPU            | 0.288 GPU               | 0.05-0.11 (TRT-LLM)             | ~0.12 CPU             |
| **Server Included**     | No                               | No                      | Yes (FastAPI, gRPC, Docker)     | Via Pipecat           |
| **License**             | OpenRAIL-M                       | Apache 2.0              | Apache 2.0                      | Apache 2.0            |
| **Pipecat Integration** | None (write custom)              | None (write custom)     | None (write custom)             | Built-in              |
| **Output SR**           | 44.1 kHz                         | 24 kHz                  | 22.05 kHz                       | 24 kHz                |
| **Maturity**            | 4 months                         | 2 months                | 2+ years                        | 1+ year               |

### Impact on Pipesong Latency Budget

Current p50 breakdown (~830ms):

- STT: 234-269ms
- LLM TTFT: 118-130ms
- TTS TTFB: 389-554ms

| Scenario            | STT   | LLM   | TTS            | **Total p50 (est.)**                     |
| ------------------- | ----- | ----- | -------------- | ---------------------------------------- |
| Current (Kokoro)    | 250ms | 125ms | 450ms          | **~830ms**                               |
| Supertonic 2 (CPU)  | 250ms | 125ms | **~20ms**      | **~400ms**                               |
| Qwen3-TTS 0.6B      | 250ms | 125ms | **~150-300ms** | **~525-675ms**                           |
| CosyVoice 3.0       | 250ms | 125ms | **~500-750ms** | **~875-1125ms**                          |
| Supertonic + LavaSR | 250ms | 125ms | ~20ms          | **~400ms** (+ better STT from denoising) |

---

## 11. Recommended Evaluation Plan

### Phase A: Quick Wins (1-2 days)

**A1. Supertonic 2 benchmark**

```bash
pip install supertonic
```

- Synthesize 20 Spanish test sentences, measure wall-clock time
- Listen for naturalness, `n` handling, number/date pronunciation
- Build minimal Pipecat adapter, measure pipeline TTFB
- **Go/No-Go**: If Spanish quality is acceptable, this is the immediate TTS upgrade

**A2. LavaSR experiment**

```bash
pip install lavasr  # or from GitHub
```

- Enhance 10 noisy 8kHz phone recordings
- A/B test Deepgram accuracy with and without enhancement
- **Go/No-Go**: If WER improves measurably on noisy calls, add to pipeline

### Phase B: Deep Evaluation (3-5 days)

**B1. Qwen3-TTS voice cloning** (only if Supertonic Spanish quality is insufficient OR voice cloning is needed)

```bash
pip install -U qwen-tts
```

- Source/record 10s Mexican Spanish reference audio
- Test voice cloning quality on 0.6B Base
- Measure VRAM alongside vLLM, measure latency on your GPU
- Test VoiceDesign on 1.7B for Mexican accent generation
- **Go/No-Go**: If clone quality + latency are acceptable, this becomes the TTS upgrade with custom voice

**B2. CosyVoice 3.0** (only if both Supertonic and Qwen3-TTS fall short)

- Docker deploy, test Spanish via gRPC streaming
- Measure real first-chunk latency on your hardware
- Test voice cloning + cross-lingual capabilities
- **Go/No-Go**: Best production infrastructure, but highest VRAM cost and potentially worse latency

### Phase C: Integration (1-2 weeks)

Based on Phase A/B results, build production Pipecat integration for the winner:

1. Custom `TTSService` subclass
2. Sentence-level streaming (reuse existing `SentenceStreamBuffer`)
3. Audio format conversion (TTS output SR -> 8kHz G.711 mulaw for Telnyx)
4. Error handling, reconnection, health checks
5. Update `MetricsCollector` for new TTS latency characteristics
6. Full pipeline latency benchmark under load

### Decision Tree

```
Start
  |
  v
[Test Supertonic 2 Spanish quality]
  |
  +-- Good enough? --> USE SUPERTONIC 2
  |     |
  |     +-- Need custom voice later? --> Supertonic Voice Builder (commercial)
  |                                      OR switch to Qwen3-TTS then
  |
  +-- Not good enough?
        |
        v
      [Test Qwen3-TTS voice clone with Mexican ref]
        |
        +-- Quality + latency OK? --> USE QWEN3-TTS 0.6B
        |     |
        |     +-- Need more features? --> Upgrade to 1.7B (VoiceDesign)
        |
        +-- VRAM too tight or latency too high?
              |
              v
            [Test CosyVoice 3.0 via Docker]
              |
              +-- Acceptable? --> USE COSYVOICE 3.0
              |
              +-- Not acceptable? --> KEEP KOKORO, revisit in 2 months
```

---

## Appendix: Sources

- Supertonic 2: [GitHub](https://github.com/supertone-inc/supertonic), [HuggingFace](https://huggingface.co/Supertone/supertonic-2), [arXiv:2503.23108](https://arxiv.org/abs/2503.23108)
- Qwen3-TTS: [GitHub](https://github.com/QwenLM/Qwen3-TTS), [HuggingFace](https://huggingface.co/collections/Qwen/qwen3-tts), [arXiv:2601.15621](https://arxiv.org/abs/2601.15621)
- NeuTTS: [GitHub](https://github.com/neuphonic/neutts), [HuggingFace](https://huggingface.co/neuphonic/neutts-nano)
- KokoClone: [GitHub](https://github.com/Ashish-Patnaik/kokoclone), [HuggingFace](https://huggingface.co/PatnaikAshish/kokoclone)
- Fun-CosyVoice 3.0: [GitHub](https://github.com/FunAudioLLM/CosyVoice), [HuggingFace](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512), [arXiv:2505.17589](https://arxiv.org/abs/2505.17589)
- Chatterbox: [GitHub](https://github.com/resemble-ai/chatterbox)
- FunASR: [GitHub](https://github.com/modelscope/FunASR)
- LavaSR: [GitHub](https://github.com/ysharma3501/LavaSR), [HuggingFace](https://huggingface.co/YatharthS/LavaSR)
