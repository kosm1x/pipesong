# Upgrade Plan — Deepgram Flux (turn-taking) + Pipecat 1.x

**Date:** 2026-06-17
**Scope:** Replace Nova-3 + Silero/Smart-Turn endpointing with Deepgram **Flux** (model-integrated end-of-turn), which requires the **Pipecat 0.0.106 → 1.x** upgrade.
**Status:** PREPARED — not applied. Gated on (a) Python 3.11+ on the GPU box, (b) the Phase 4a `4a.6` baseline run, (c) a Flux-vs-Nova-3 Spanish transcription A/B.

All external facts below are quoted from primary sources (Pipecat docs, Pipecat API reference, Deepgram docs), not from model memory.

---

## 1. Why this couples Flux and Pipecat 1.x

- Flux's Pipecat service (`DeepgramFluxSTTService`) ships in the 1.x service tree (`pipecat.services.deepgram.flux`). It is not available in 0.0.106.
- Flux owns turn detection, so the docs say to use `ExternalUserTurnStrategies` — a 1.x turn-management primitive (`pipecat.turns.user_turn_strategies`).

So you cannot adopt Flux without moving to Pipecat 1.x. They are one unit of work.

## 2. The critical nuance — Flux REPLACES Nova-3, it is not an add-on

Flux is a single integrated model that does **transcription + turn detection together**. You do not get "Nova-3 transcription + Flux turn-taking." Adopting Flux means:

- STT transcription switches from **Nova-3 (`language="es"`, proven on real calls)** to **Flux `flux-general-multi` + Spanish hint** (unproven on Mexican-Spanish telephony here).
- Therefore the evaluation must A/B **transcription quality**, not just measure the ~100-200ms endpoint-latency win. A turn-latency win that regresses Spanish WER is a net loss.

Flux uses a **separate v2 endpoint** (`wss://api.deepgram.com/v2/listen`) and is a distinct product from Nova-3 — **confirm Flux pricing vs the $0.0043/min Nova-3 line** in PLAN.md §2 before committing.

## 3. Pipecat 1.0 migration delta — small for THIS codebase

Source: <https://docs.pipecat.ai/pipecat/migration/migration-1.0>

| 1.0 breaking change                                       | pipesong today                                                                         | Action                         |
| --------------------------------------------------------- | -------------------------------------------------------------------------------------- | ------------------------------ |
| Submodule import paths (`pipecat.services.openai.llm`, …) | **Already used** (`pipeline.py:19-21`)                                                 | none                           |
| Universal `LLMContext` replaces `OpenAILLMContext`        | **Already used** (`LLMContext`, `LLMContextAggregatorPair`, `LLMUserAggregatorParams`) | none                           |
| VAD configured on the user aggregator                     | **Already used** (`vad_analyzer=` on `LLMUserAggregatorParams`)                        | replaced by Flux turn strategy |
| Python 3.11+ required (3.10 dropped)                      | unknown on GPU box                                                                     | **VERIFY**                     |
| `VADParams.stop_secs` default 0.8 → 0.2                   | uses `VADParams()` default                                                             | moot once Flux owns turns      |

Residual code risks (NOT covered by the migration guide — verify after install):

- `OpenAILLMService.Settings(...)`, `KokoroTTSService.Settings(...)` nested-class pattern and the `text_aggregation_mode=` kwarg (`pipeline.py:84-115`). The `.Settings` pattern is confirmed retained for `DeepgramFluxSTTService.Settings` in 1.x; confirm the same for LLM/TTS.
- Custom `FrameProcessor.process_frame(self, frame, direction)` signature and `pipecat.frames.frames` imports used across `processors.py`.

## 4. Verified Flux facts

Sources: Pipecat API reference (`pipecat.services.deepgram.flux.stt`), Deepgram docs (`/docs/flux/configuration`).

- Class / import: `from pipecat.services.deepgram.flux import DeepgramFluxSTTService`
- Models: `flux-general-en`, `flux-general-multi` (Spanish supported via multilingual + `language_hint`/hints `["es"]`).
- Encoding: `flux_encoding="linear16"` (required). `sample_rate` defaults to pipeline rate.
- End-of-turn params (in `DeepgramFluxSTTService.Settings`):
  - `eot_threshold` — 0.5–0.9, default **0.7** (lower = faster turns, more false ends)
  - `eager_eot_threshold` — 0.3–0.9, **off by default**; enables predicted EOT pushed as interim frames (faster responses, **more LLM calls / cost**)
  - `eot_timeout_ms` — 500–10000, default **5000**
- `should_interrupt` (default `True`) handles barge-in — replaces Silero's interruption role.
- Turn wiring: `LLMUserAggregatorParams(user_turn_strategies=ExternalUserTurnStrategies())` — removes `SileroVADAnalyzer` turn-taking and Smart Turn v3.

## 5. Proposed code changes (review before apply)

### `requirements.txt`

```diff
-pipecat-ai[websocket,deepgram,kokoro,silero,openai]==0.0.106
+pipecat-ai[websocket,deepgram,kokoro,openai]==1.4.0   # pin latest 1.x; VERIFY extra names in 1.x
```

(`silero` extra likely droppable since Flux owns turns; keep only if Silero is still wanted for any non-Flux agent.)

### `pipeline.py` — STT

```diff
-from pipecat.services.deepgram.stt import DeepgramSTTService
+from pipecat.services.deepgram.flux import DeepgramFluxSTTService
+from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies

-    stt = DeepgramSTTService(
-        api_key=settings.deepgram_api_key,
-        audio_passthrough=True,
-        sample_rate=8000,
-        settings=DeepgramSTTService.Settings(
-            language=language,
-            model="nova-3",
-            smart_format=True,
-            interim_results=True,
-        ),
-    )
+    stt = DeepgramFluxSTTService(
+        api_key=settings.deepgram_api_key,
+        sample_rate=8000,              # VERIFY Flux accepts 8 kHz telephony audio
+        flux_encoding="linear16",
+        should_interrupt=True,
+        settings=DeepgramFluxSTTService.Settings(
+            model="flux-general-multi",   # Spanish via multilingual model
+            eot_threshold=0.7,            # tune down for faster turns
+            eager_eot_threshold=0.5,      # optional: faster, more LLM calls — A/B it
+            eot_timeout_ms=5000,
+        ),
+    )
```

### `pipeline.py` — turn management (Flux replaces VAD endpointing + Smart Turn)

```diff
-    vad_params = VADParams()
-    if vad_stop_secs is not None:
-        vad_params.stop_secs = vad_stop_secs
-    if vad_confidence is not None:
-        vad_params.confidence = vad_confidence
-
     context = LLMContext()
     user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
         context,
         user_params=LLMUserAggregatorParams(
-            vad_analyzer=SileroVADAnalyzer(sample_rate=8000, params=vad_params),
+            user_turn_strategies=ExternalUserTurnStrategies(),
         ),
     )
```

Knock-on: the Phase 4a per-agent `vad_stop_secs` / `vad_confidence` columns become Flux-irrelevant; map agent turn-tuning to `eot_threshold` / `eager_eot_threshold` / `eot_timeout_ms` instead (new agent columns, or reuse). `MetricsCollector` keeps working — but `stt_ms` semantics change (Flux EOT vs Deepgram final), so re-baseline.

## 6. Operator validation checklist (on the GPU box, in order)

1. [ ] Confirm Python ≥ 3.11 on the runtime (1.0 drops 3.10).
2. [ ] **Run the Phase 4a `4a.6` baseline FIRST** on current Nova-3/Kokoro — capture p50 stt/llm/tts/e2e. No before-number = no way to prove Flux helped.
3. [ ] Branch, install `pipecat-ai==1.x`; verify extras names; `python -c` import-smoke `OpenAILLMService.Settings`, `KokoroTTSService` + `text_aggregation_mode`, custom `FrameProcessor.process_frame`.
4. [ ] Run the full test suite; fix any 1.x signature drift in `processors.py`.
5. [ ] Confirm Flux pricing vs Nova-3; confirm Flux accepts 8 kHz `linear16` telephony audio.
6. [ ] **Flux-multi vs Nova-3-es transcription A/B** on recorded Mexican-Spanish calls (WER) — go/no-go gate, not just latency.
7. [ ] Measure endpoint-latency delta vs step-2 baseline; tune `eot_threshold` / `eager_eot_threshold`.
8. [ ] Update README stack table + PROGRESS.md + this doc with results.

## 7. Sources

- Pipecat 1.0 migration: <https://docs.pipecat.ai/pipecat/migration/migration-1.0>
- Pipecat Deepgram (Flux) service: <https://docs.pipecat.ai/server/services/stt/deepgram>
- Flux STT API reference: <https://reference-server.pipecat.ai/en/latest/api/pipecat.services.deepgram.flux.stt.html>
- ExternalUserTurnStrategies: <https://docs.pipecat.ai/api-reference/server/utilities/turn-management/user-turn-strategies>
- Deepgram Flux config / EOT params: <https://developers.deepgram.com/docs/flux/configuration>
- Deepgram + Pipecat integration: <https://developers.deepgram.com/docs/pipecat-integration>

---

## 8. Branch status + QA audit (2026-06-17)

_Branch rebuilt cleanly from `main` via the Python ship process (`/ship-it-py`): env recon → static-only checks → carried-forward qa-audit → docs → commit. Supersedes the earlier `/ship-it` attempt (commit `365d221`)._

**Applied on branch `flux-pipecat1x` — UNVALIDATED (no GPU, pipecat 1.x not installed here, no test suite). Syntax-only check passed; not run, not deployed, not merged. Do NOT merge until the gate below passes on the TensorDock box.**

Changed: `requirements.txt` (pipecat-ai → 1.4.0, dropped `silero` extra), `src/pipesong/pipeline.py` (Flux STT + `ExternalUserTurnStrategies`).

qa-auditor verdict: **PASS WITH WARNINGS**. It cleared three feared 1.x breaks (good news — migration surface is small): `TextAggregationMode` + `text_aggregation_mode` still exist; `FrameProcessor.process_frame(self, frame, direction)` unchanged; `LLMContext.get_messages/add_message/set_messages` all present. Flux constructor arg placement (`flux_encoding`, `should_interrupt` top-level; `language_hints` plural in `.Settings`) verified correct.

Fixed in-branch: eager-EOT left OFF (cost regression avoided); `vad_*` now logs a warning instead of silently no-op'ing; module docstring updated.

**Consolidated merge gate (must pass on the GPU box, in order):**

1. [ ] Python ≥ 3.11 on runtime.
2. [ ] **Phase 4a `4a.6` baseline FIRST** (Nova-3/Kokoro before-numbers).
3. [ ] `pip install pipecat-ai==1.4.0`; verify extra names; import-smoke `pipesong.pipeline`/`main`/`processors`.
4. [ ] **C1 (blocking): confirm Flux accepts 8 kHz `linear16`** — Telnyx delivers 8 kHz PCMU; all Flux examples use 16 kHz. If Flux needs 16 kHz, serializer must upsample or this is dead.
5. [ ] **W1: confirm `KokoroTTSService.Settings(language="es")` constructs** under 1.x (docs example uses `Language.ES` enum) — switch if it raises.
6. [ ] **W4: confirm call recording still captures caller audio** (the old `audio_passthrough=True` flag was dropped).
7. [ ] **S1: decide `vad_stop_secs`/`vad_confidence`** — map onto `eot_threshold`/`eager_eot_threshold` or remove from API+DB so operators aren't silently no-op'd.
8. [ ] **I1: re-baseline `stt_ms`** — Flux EOT-based latency is not comparable to Nova-3-final.
9. [ ] Flux pricing vs $0.0043/min Nova-3 + **Flux-multi vs Nova-3-es Spanish WER A/B** — go/no-go on transcription quality.
10. [ ] Then update README stack table + PROGRESS.md with measured results.
