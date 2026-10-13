"""A.1 phrase corpus + mixer: keying, loading, frame contract, context and ordering."""
import json
import logging
import unicodedata

import pytest
from pipecat.frames.frames import (
    EndFrame,
    LLMAssistantPushAggregationFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.tests.utils import SleepFrame, run_test

from pipesong.prerecorded.corpus import Corpus, normalize_phrase, phrase_key
from pipesong.prerecorded.mixer import PreRecordedClipEmitter, PreRecordedClipFrame, PreRecordedMixer
from pipesong.processors import SentenceStreamBuffer, SpanishOnlyFilter
from tests.test_disclosure_clip import _play, _RecordingOutput, _tone, _write_wav

VOICE = "em_alex"
ES = "Permítame verificar esa información."
EN = "One moment please."


def _make_corpus(base, phrases, voice=VOICE, *, wav=True):
    """Write clips + manifest the way scripts/generate_clips.py does; return {text: audio}."""
    corpus_dir = base / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = corpus_dir / "manifest.json"
    entries = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    audio = {}
    for i, text in enumerate(phrases):
        key = phrase_key(voice, text)
        if wav:
            audio[text] = _tone(4800 + 240 * i)
            _write_wav(corpus_dir / f"{key}.wav", audio[text], rate=24000)
        entries.append({"key": key, "voice": voice, "text": text})
    manifest_path.write_text(json.dumps(entries, ensure_ascii=False))
    return audio


# --- keying -----------------------------------------------------------------


def test_normalization_ignores_case_punctuation_whitespace_and_unicode_form():
    base = normalize_phrase("¿Podría repetirme su nombre?")
    assert base == "podría repetirme su nombre ?"
    nfd = unicodedata.normalize("NFD", "¿Podría repetirme su nombre?")
    for variant in (
        nfd,
        "¿PODRÍA repetirme su nombre?",
        "  ¿podría   repetirme\tsu nombre ",
        "Podría repetirme su nombre?",
        "¿Podría repetirme su nombre.",  # SentenceStreamBuffer's cut of "…nombre, por favor?"
        "¡¿Podría repetirme su nombre?!…",  # question wins over exclamation
        "«¿Podría repetirme su nombre?»",
    ):
        assert normalize_phrase(variant) == base, variant
        assert phrase_key(VOICE, variant) == phrase_key(VOICE, "¿Podría repetirme su nombre?")


def test_sentence_type_stays_in_the_key():
    # W1: same words, opposite meaning: a question must never play the statement's clip.
    statement = phrase_key(VOICE, "Su cita ha quedado confirmada.")
    question = phrase_key(VOICE, "¿Su cita ha quedado confirmada?")
    exclamation = phrase_key(VOICE, "¡Su cita ha quedado confirmada!")
    assert len({statement, question, exclamation}) == 3
    assert phrase_key(VOICE, "Su cita ha quedado confirmada") == statement
    assert phrase_key(VOICE, "Su cita ha quedado confirmada?") == question
    assert phrase_key(VOICE, "Su cita ha quedado confirmada!") == exclamation


def test_question_line_still_hits_its_own_clip(tmp_path):
    _make_corpus(tmp_path, ["¿Podría repetirme su nombre?", "Su cita ha quedado confirmada."])
    corpus = Corpus.load(tmp_path, VOICE)
    assert corpus.lookup("¿Podría repetirme su nombre?") is not None
    assert corpus.lookup("¿Podría repetirme su nombre.") is not None
    assert corpus.lookup("Podría repetirme su nombre.") is None
    assert corpus.lookup("¿Su cita ha quedado confirmada?") is None
    assert corpus.lookup("¡Su cita ha quedado confirmada!") is None


def test_different_words_accents_or_voice_change_the_key():
    key = phrase_key(VOICE, ES)
    assert len(key) == 16 and int(key, 16) >= 0
    assert phrase_key(VOICE, "Permítame verificar su información.") != key
    assert phrase_key(VOICE, "Permitame verificar esa informacion.") != key  # accents are words
    assert phrase_key("ef_dora", ES) != key
    assert phrase_key(f" {VOICE}\n", ES) == key


# --- loading / lookup ---------------------------------------------------------


def test_exact_hit_returns_a_fresh_tts_audio_frame(tmp_path):
    audio = _make_corpus(tmp_path, [ES])
    corpus = Corpus.load(tmp_path, VOICE)
    assert len(corpus) == 1

    a = corpus.lookup("permítame verificar esa información")
    b = corpus.lookup(ES)
    assert isinstance(a, TTSAudioRawFrame)
    assert a.audio == audio[ES] and a.sample_rate == 24000 and a.num_channels == 1
    assert a is not b and a.id != b.id  # a phrase can play several times per call


def test_near_miss_is_a_miss(tmp_path):
    _make_corpus(tmp_path, [ES])
    corpus = Corpus.load(tmp_path, VOICE)
    assert corpus.lookup("Permítame verificar su información.") is None
    assert corpus.lookup("Permítame verificar esa información ahora.") is None
    assert corpus.lookup("verificar esa información.") is None


def test_english_and_spanish_phrases(tmp_path):
    _make_corpus(tmp_path, [ES, EN])
    corpus = Corpus.load(tmp_path, VOICE)
    assert corpus.lookup("One moment, please.") is not None
    assert corpus.lookup("one moment please") is not None
    assert corpus.lookup("One moment please!") is None
    assert corpus.lookup("One more moment please.") is None
    assert corpus.lookup(ES) is not None


@pytest.mark.parametrize("setup", ["no_dir", "empty_dir", "empty_manifest", "corrupt_manifest", "not_a_list"])
def test_empty_or_broken_corpus_never_raises(tmp_path, setup):
    corpus_dir = tmp_path / "corpus"
    if setup != "no_dir":
        corpus_dir.mkdir()
    if setup == "empty_manifest":
        (corpus_dir / "manifest.json").write_text("[]")
    elif setup == "corrupt_manifest":
        (corpus_dir / "manifest.json").write_text("{not json")
    elif setup == "not_a_list":
        (corpus_dir / "manifest.json").write_text('{"key": "x"}')
    corpus = Corpus.load(tmp_path, VOICE)
    assert len(corpus) == 0
    assert corpus.lookup(ES) is None


def test_voice_mismatch_is_a_miss(tmp_path):
    _make_corpus(tmp_path, [ES], voice=VOICE)
    assert Corpus.load(tmp_path, "ef_dora").lookup(ES) is None
    assert Corpus.load(tmp_path, f" {VOICE} ").lookup(ES) is not None


def test_manifest_entry_without_wav_is_skipped(tmp_path):
    _make_corpus(tmp_path, [ES])
    _make_corpus(tmp_path, [EN], wav=False)
    corpus = Corpus.load(tmp_path, VOICE)
    assert len(corpus) == 1
    assert corpus.lookup(EN) is None


def test_wav_without_manifest_entry_is_ignored(tmp_path):
    corpus_dir = tmp_path / "corpus"
    _write_wav(corpus_dir / f"{phrase_key(VOICE, ES)}.wav", _tone(2400))
    (corpus_dir / "manifest.json").write_text("[]")
    assert Corpus.load(tmp_path, VOICE).lookup(ES) is None


def test_manifest_key_that_does_not_match_its_text_is_skipped(tmp_path):
    _make_corpus(tmp_path, [ES])
    manifest = tmp_path / "corpus" / "manifest.json"
    entries = json.loads(manifest.read_text())
    entries[0]["text"] = "Su cita ha quedado cancelada."  # clip says something else
    manifest.write_text(json.dumps(entries, ensure_ascii=False))
    corpus = Corpus.load(tmp_path, VOICE)
    assert len(corpus) == 0
    assert corpus.lookup("Su cita ha quedado cancelada.") is None


def test_clip_at_another_sample_rate_is_skipped(tmp_path):
    # W2: the output transport's resampler is shared per sender and raises on a rate change.
    _make_corpus(tmp_path, [ES], wav=False)
    _write_wav(tmp_path / "corpus" / f"{phrase_key(VOICE, ES)}.wav", _tone(3200, rate=16000), rate=16000)
    assert len(Corpus.load(tmp_path, VOICE)) == 0


def test_disclosure_clip_at_another_sample_rate_returns_none(tmp_path):
    from pipesong.prerecorded.disclosure import disclosure_clip_path, load_disclosure_clip

    text = "Esta llamada está siendo grabada."
    _write_wav(disclosure_clip_path(tmp_path, VOICE, text), _tone(3200, rate=16000), rate=16000)
    assert load_disclosure_clip(tmp_path, VOICE, text) is None


def test_invalid_wav_is_skipped(tmp_path):
    _make_corpus(tmp_path, [ES], wav=False)
    _write_wav(tmp_path / "corpus" / f"{phrase_key(VOICE, ES)}.wav", _tone(200) * 2, channels=2)
    assert len(Corpus.load(tmp_path, VOICE)) == 0


# --- mixer + emitter frame contract -------------------------------------------


class _Recorder(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.frames = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            self.frames.append(frame)
        await self.push_frame(frame, direction)


_TTS_SHAPED = (TTSStartedFrame, TTSAudioRawFrame, TTSTextFrame, TTSStoppedFrame, LLMAssistantPushAggregationFrame)


async def test_mixer_passes_a_miss_through_unchanged(tmp_path):
    _make_corpus(tmp_path, [ES])
    mixer = PreRecordedMixer(Corpus.load(tmp_path, VOICE))
    rec = _Recorder()
    speak = TTSSpeakFrame(text="Su cita es el martes a las diez.")

    await run_test(Pipeline([mixer, rec]), frames_to_send=[speak])

    assert [f for f in rec.frames if isinstance(f, TTSSpeakFrame)] == [speak]
    assert not [f for f in rec.frames if isinstance(f, (PreRecordedClipFrame, *_TTS_SHAPED))]
    assert (mixer.hits, mixer.misses) == (0, 1)


@pytest.mark.parametrize(
    "append_to_context, before, expect_push_aggregation",
    [
        # Standalone TTSSpeakFrame commits its own turn. pipecat 1.7 TTSService only does
        # this before the call's first LLM response (see the emitter); no pipesong path
        # sends an append_to_context TTSSpeakFrame outside a response today.
        (True, [], True),
        (True, [LLMFullResponseStartFrame()], False),  # inside an LLM response: End commits
        (False, [], False),  # fillers / disclosure stay out of context
    ],
)
async def test_hit_becomes_the_frames_tts_emits_for_a_spoken_sentence(
    tmp_path, append_to_context, before, expect_push_aggregation
):
    audio = _make_corpus(tmp_path, [ES])
    mixer = PreRecordedMixer(Corpus.load(tmp_path, VOICE))
    rec = _Recorder()

    await run_test(
        Pipeline([mixer, PreRecordedClipEmitter(), rec]),
        frames_to_send=[*before, TTSSpeakFrame(text=ES, append_to_context=append_to_context)],
    )

    seen = [f for f in rec.frames if isinstance(f, (TTSSpeakFrame, PreRecordedClipFrame, *_TTS_SHAPED))]
    names = ["TTSStartedFrame", "TTSAudioRawFrame", "TTSTextFrame", "TTSStoppedFrame"]
    if expect_push_aggregation:
        names.append("LLMAssistantPushAggregationFrame")
    assert [type(f).__name__ for f in seen] == names
    started, clip, text, stopped = seen[:4]
    assert started.context_id and started.context_id == clip.context_id == text.context_id == stopped.context_id
    assert started.append_to_context is append_to_context
    assert text.text == ES and text.aggregated_by == "sentence" and text.append_to_context is append_to_context
    assert clip.audio == audio[ES] and clip.sample_rate == 24000
    assert (mixer.hits, mixer.misses) == (1, 0)


async def test_mixer_logs_totals_at_end(tmp_path, caplog):
    _make_corpus(tmp_path, [ES])
    mixer = PreRecordedMixer(Corpus.load(tmp_path, VOICE))
    with caplog.at_level(logging.INFO, logger="pipesong.prerecorded.mixer"):
        await run_test(
            Pipeline([mixer, PreRecordedClipEmitter()]),
            frames_to_send=[TTSSpeakFrame(text=ES), TTSSpeakFrame(text="Otra cosa."), TTSSpeakFrame(text=ES)],
        )
    messages = [r.getMessage() for r in caplog.records if r.name == "pipesong.prerecorded.mixer"]
    hits = [r for r in caplog.records if r.getMessage().startswith(f"mixer: hit {phrase_key(VOICE, ES)}")]
    assert len(hits) == 2 and all(r.levelno == logging.INFO for r in hits)
    assert "mixer: 2 hit(s), 1 miss(es), corpus of 1 clip(s)" in messages


async def test_hit_starts_and_completes_a_bot_turn_on_the_real_transport(tmp_path):
    _make_corpus(tmp_path, [ES])
    mixer = PreRecordedMixer(Corpus.load(tmp_path, VOICE))

    out = await _play([TTSSpeakFrame(text=ES, append_to_context=False)], before=(mixer, PreRecordedClipEmitter()))

    assert out.events == ["BotStartedSpeakingFrame", "BotStoppedSpeakingFrame", "EndFrame"]
    assert out.written and all(f.sample_rate == 8000 for f in out.written)
    assert any(isinstance(f, TTSAudioRawFrame) for f in out.written)


# --- real chain: filter → sentence buffer → mixer → Kokoro → emitter → transport → aggregator ----

LLM_TOKENS = ["Con gusto le ayudo", ". Permítame verificar ", "esa información. Su cita ", "es el martes", "."]
SENTENCES = ["Con gusto le ayudo.", "Permítame verificar esa información.", "Su cita es el martes."]


async def _speak_llm_response(corpus_base):
    """One LLM response through the real processors around the live TTS service.

    Returns (context messages, frames seen between the emitter and the transport).
    """
    from pipecat.services.kokoro.tts import KokoroTTSService

    tts = KokoroTTSService(
        settings=KokoroTTSService.Settings(voice=VOICE, language="es")
    )
    context = LLMContext()
    _, assistant = LLMContextAggregatorPair(context)
    rec = _Recorder()
    await run_test(
        Pipeline([
            SpanishOnlyFilter(),
            SentenceStreamBuffer(),
            PreRecordedMixer(Corpus.load(corpus_base, VOICE)),
            tts,
            PreRecordedClipEmitter(),
            rec,
            _RecordingOutput(),
            assistant,
        ]),
        frames_to_send=[
            LLMFullResponseStartFrame(),
            *[LLMTextFrame(text=t) for t in LLM_TOKENS],
            LLMFullResponseEndFrame(),
            SleepFrame(sleep=0.5),
        ],
        pipeline_params=PipelineParams(audio_out_sample_rate=8000),
    )
    return context.get_messages(), rec.frames


async def test_clip_lands_in_context_and_order_exactly_like_tts(tmp_path):
    audio = _make_corpus(tmp_path / "with", [SENTENCES[1]])
    messages_tts, _ = await _speak_llm_response(tmp_path / "without")  # empty corpus: all Kokoro
    messages, frames = await _speak_llm_response(tmp_path / "with")

    # Step 1: the assistant turn in the context is the same as when Kokoro speaks it all.
    assert messages_tts[-1] == {"role": "assistant", "content": " ".join(SENTENCES)}
    assert messages == messages_tts

    # Step 2: the clip sits between sentence 1's and sentence 3's Kokoro audio and no
    # context interleaves with another (each Started..Stopped run is contiguous).
    texts = [f.text for f in frames if isinstance(f, TTSTextFrame)]
    assert texts == SENTENCES
    runs = []
    for f in frames:
        if isinstance(f, (TTSStartedFrame, TTSAudioRawFrame, TTSTextFrame, TTSStoppedFrame)):
            if not runs or runs[-1][0] != f.context_id:
                runs.append((f.context_id, []))
            runs[-1][1].append(f)
    assert len(runs) == 3
    for _, run in runs:
        assert isinstance(run[0], TTSStartedFrame) and isinstance(run[-1], TTSStoppedFrame)
    clip_audio = [f.audio for f in runs[1][1] if isinstance(f, TTSAudioRawFrame)]
    assert clip_audio == [audio[SENTENCES[1]]]


# --- generator manifest merge / prune (W3) -----------------------------------


def _generate_clips():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "generate_clips.py"
    spec = importlib.util.spec_from_file_location("generate_clips", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_merge_manifest_is_idempotent_sorted_and_keeps_other_voices():
    merge = _generate_clips().merge_manifest
    dora = {"key": phrase_key("ef_dora", ES), "voice": "ef_dora", "text": ES}
    once, pruned = merge([dora], VOICE, [ES, EN], prune=False)
    twice, _ = merge(once, VOICE, [ES, EN], prune=False)
    assert once == twice and pruned == []
    assert [e["key"] for e in once] == sorted(e["key"] for e in once)
    assert dora in once and len(once) == 3


def test_merge_manifest_prune_drops_only_this_voices_removed_lines():
    merge = _generate_clips().merge_manifest
    dora = {"key": phrase_key("ef_dora", EN), "voice": "ef_dora", "text": EN}
    stale = {"key": "0123456789abcdef", "voice": VOICE, "text": "Línea con clave vieja."}
    start, _ = merge([dora, stale], VOICE, [ES, EN], prune=False)

    kept, pruned = merge(start, VOICE, [ES], prune=False)  # without --prune: unchanged
    assert kept == start and pruned == []

    kept, pruned = merge(start, VOICE, [ES], prune=True)
    assert {e["text"] for e in pruned} == {EN, stale["text"]}
    assert [e["key"] for e in pruned] == sorted(e["key"] for e in pruned)
    assert {e["key"] for e in kept} == {phrase_key(VOICE, ES), dora["key"]}


# --- metrics: synthesis TTFB, TTFA, hit count -----------------------------------


async def test_hit_reports_ttfb_that_metrics_collector_keeps_for_the_turn(tmp_path):
    from pipecat.frames.frames import MetricsFrame
    from pipecat.metrics.metrics import TTFBMetricsData

    _make_corpus(tmp_path, [ES])
    emitter = PreRecordedClipEmitter()
    rec = _Recorder()
    rows = []
    collector = _collector(rows)  # writes its row on run_test's EndFrame
    later_kokoro = MetricsFrame(data=[TTFBMetricsData(processor="KokoroTTSService#0", value=0.8)])

    await run_test(
        Pipeline([PreRecordedMixer(Corpus.load(tmp_path, VOICE)), emitter, rec, collector]),
        # MetricsFrame is a SystemFrame and overtakes queued control frames: leave the
        # LLM's time to first sentence between the response start and the clip, as live.
        frames_to_send=[LLMFullResponseStartFrame(), SleepFrame(sleep=0.05), TTSSpeakFrame(text=ES), SleepFrame(sleep=0.1), later_kokoro],
    )

    ttfb = [m for f in rec.frames if isinstance(f, MetricsFrame) for m in f.data if isinstance(m, TTFBMetricsData)]
    assert [m.processor for m in ttfb] == [emitter.name, "KokoroTTSService#0"]
    assert 0 <= ttfb[0].value < 0.05
    # The response's FIRST synthesis TTFB is the clip's (~0), not the later Kokoro sentence's.
    assert len(rows) == 1
    assert rows[0].tts_ttfb_ms == ttfb[0].value * 1000


# --- barge-in: an interrupted clip never reaches the context ------------------


class _RealtimeOutput(_RecordingOutput):
    """Paces writes like a phone line so an interruption can land mid-clip."""

    async def write_audio_frame(self, frame):
        import asyncio

        await asyncio.sleep(len(frame.audio) / 2 / 8000)
        return await super().write_audio_frame(frame)


@pytest.mark.parametrize("interrupt", [False, True])
async def test_barge_in_mid_clip_keeps_the_clip_text_out_of_context(tmp_path, interrupt):
    from pipecat.frames.frames import InterruptionFrame

    phrase = "Su cita ha quedado confirmada."
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "manifest.json").write_text(
        json.dumps([{"key": phrase_key(VOICE, phrase), "voice": VOICE, "text": phrase}], ensure_ascii=False)
    )
    _write_wav(corpus_dir / f"{phrase_key(VOICE, phrase)}.wav", _tone(24000), rate=24000)  # 1.0 s

    context = LLMContext()
    _, assistant = LLMContextAggregatorPair(context)
    out = _RealtimeOutput()
    await run_test(
        Pipeline([PreRecordedMixer(Corpus.load(tmp_path, VOICE)), PreRecordedClipEmitter(), out, assistant]),
        frames_to_send=[
            LLMFullResponseStartFrame(),
            TTSSpeakFrame(text=phrase),
            LLMFullResponseEndFrame(),
            SleepFrame(sleep=0.3),  # ~0.3 s of the 1.0 s clip played
            *([InterruptionFrame()] if interrupt else []),
            SleepFrame(sleep=1.2),
        ],
        pipeline_params=PipelineParams(audio_out_sample_rate=8000),
    )

    played = sum(len(f.audio) for f in out.written if isinstance(f, TTSAudioRawFrame)) / 2 / 8000
    spoken = [m for m in context.get_messages() if m.get("role") == "assistant"]
    if interrupt:
        assert played < 0.6  # cut mid-clip
        assert spoken == []
    else:
        assert played > 0.9
        assert spoken == [{"role": "assistant", "content": phrase}]


class _Session:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, row):
        self.rows.append(row)

    async def commit(self):
        pass


def _collector(rows):
    from pipesong.processors import MetricsCollector

    return MetricsCollector(call_id=None, session_factory=lambda: _Session(rows))


def _kokoro_ttfb(seconds):
    from pipecat.frames.frames import MetricsFrame
    from pipecat.metrics.metrics import TTFBMetricsData

    return MetricsFrame(data=[TTFBMetricsData(processor="KokoroTTSService#0", value=seconds)])


def _kokoro_sentence(seconds):
    """What KokoroTTSService pushes for one spoken sentence (audio long enough to start the bot)."""
    return [
        TTSStartedFrame(context_id="k"),
        _kokoro_ttfb(seconds),
        TTSAudioRawFrame(audio=_tone(4800), sample_rate=24000, num_channels=1, context_id="k"),
        TTSStoppedFrame(context_id="k"),
    ]


@pytest.mark.parametrize("hit", [True, False])
async def test_ttfa_runs_from_user_eot_to_bot_started_upstream(tmp_path, hit):
    from pipecat.frames.frames import UserStoppedSpeakingFrame

    _make_corpus(tmp_path, [ES])
    rows = []
    speech = [TTSSpeakFrame(text=ES)] if hit else _kokoro_sentence(0.3)

    # Real output transport after the collector: BotStartedSpeakingFrame comes upstream
    # from it, exactly where it does in create_pipeline.
    await run_test(
        Pipeline([PreRecordedMixer(Corpus.load(tmp_path, VOICE)), PreRecordedClipEmitter(), _collector(rows), _RecordingOutput()]),
        frames_to_send=[
            UserStoppedSpeakingFrame(),
            SleepFrame(sleep=0.2),  # LLM + TTS time the caller waits through
            LLMFullResponseStartFrame(),
            SleepFrame(sleep=0.05),  # time to first sentence (see the TTFB test above)
            *speech,
            LLMFullResponseEndFrame(),
            SleepFrame(sleep=0.5),
        ],
        pipeline_params=PipelineParams(audio_out_sample_rate=8000),
    )

    assert len(rows) == 1
    row = rows[0]
    assert 200 <= row.ttfa_ms < 600
    assert row.prerecorded_hits == (1 if hit else 0)
    if hit:
        assert row.tts_ttfb_ms < 50  # synthesis TTFB of a clip: ~0, never the TTFA
    else:
        assert row.tts_ttfb_ms == pytest.approx(300)


async def test_ttfa_is_recorded_once_per_turn_and_hits_are_counted(tmp_path):
    from pipecat.frames.frames import UserStoppedSpeakingFrame

    filler, answer = "Un momento.", ES
    _make_corpus(tmp_path, [filler, answer])
    rows = []

    await run_test(
        Pipeline([PreRecordedMixer(Corpus.load(tmp_path, VOICE)), PreRecordedClipEmitter(), _collector(rows), _RecordingOutput()]),
        frames_to_send=[
            UserStoppedSpeakingFrame(),
            SleepFrame(sleep=0.1),
            LLMFullResponseStartFrame(),
            SleepFrame(sleep=0.05),
            TTSSpeakFrame(text=filler),
            SleepFrame(sleep=0.6),  # filler plays and the bot stops before the next clip
            TTSSpeakFrame(text=answer),
            LLMFullResponseEndFrame(),
            SleepFrame(sleep=0.5),
        ],
        pipeline_params=PipelineParams(audio_out_sample_rate=8000),
    )

    assert len(rows) == 1
    # The first BotStartedSpeaking (filler) sets it; the second (answer, >= 700 ms) does not.
    assert 100 <= rows[0].ttfa_ms < 500
    assert rows[0].prerecorded_hits == 2


@pytest.mark.parametrize("tool_turn", [False, True])
async def test_tts_ttfb_is_the_responses_first_and_ignores_out_of_response_speech(tool_turn):
    rows = []
    frames = [_kokoro_ttfb(0.9)]  # disclosure / filler before any response: ignored
    if tool_turn:
        # Tool call: the first response's end is swallowed by ToolCallProcessor, and the
        # filler is spoken inside it; the follow-up response restarts the window.
        frames += [LLMFullResponseStartFrame(), _kokoro_ttfb(0.7)]
    frames += [LLMFullResponseStartFrame(), _kokoro_ttfb(0.3), _kokoro_ttfb(0.5), LLMFullResponseEndFrame()]

    # Live, LLM and synthesis time separate these; a burst would let the (system)
    # MetricsFrames overtake the (control) response frames.
    paced = [x for f in frames for x in (f, SleepFrame(sleep=0.05))]
    await run_test(Pipeline([_collector(rows)]), frames_to_send=paced)

    assert len(rows) == 1
    assert rows[0].tts_ttfb_ms == pytest.approx(300)
    assert rows[0].ttfa_ms is None  # no user EOT in this turn (e.g. turn 0)
    assert rows[0].prerecorded_hits == 0


async def test_end_call_turn_is_written_at_end_frame(tmp_path):
    # end_call: ToolCallProcessor swallows the response's LLMFullResponseEndFrame and speaks
    # the reason; the call then ends, so the row can only be written on EndFrame.
    from pipecat.frames.frames import UserStoppedSpeakingFrame

    reason = "Gracias por su llamada."
    _make_corpus(tmp_path, [reason])
    rows = []

    await run_test(
        Pipeline([PreRecordedMixer(Corpus.load(tmp_path, VOICE)), PreRecordedClipEmitter(), _collector(rows), _RecordingOutput()]),
        frames_to_send=[
            UserStoppedSpeakingFrame(),
            SleepFrame(sleep=0.2),
            LLMFullResponseStartFrame(),
            SleepFrame(sleep=0.05),
            _kokoro_ttfb(0.4),
            SleepFrame(sleep=0.05),
            TTSSpeakFrame(text=reason, append_to_context=False),
            SleepFrame(sleep=0.5),
        ],  # run_test then sends EndFrame
        pipeline_params=PipelineParams(audio_out_sample_rate=8000),
    )

    assert len(rows) == 1
    assert 200 <= rows[0].ttfa_ms < 600
    assert rows[0].prerecorded_hits == 1
