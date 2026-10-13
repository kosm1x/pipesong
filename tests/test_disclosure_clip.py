"""A.0 pre-recorded disclosure: clip keying, loading, and the transport contract."""
import math
import struct
import unicodedata
import wave

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStoppedFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.stt_service import STTService
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams

from pipesong.prerecorded.disclosure import (
    DisclosureClipPlayer,
    disclosure_clip_path,
    load_disclosure_clip,
)

TEXT = "Esta llamada está siendo grabada para fines de calidad y entrenamiento."
VOICE = "em_alex"


def _tone(n_frames: int, rate: int = 24000) -> bytes:
    return b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(n_frames)
    )


def _write_wav(path, frames: bytes, *, channels=1, sampwidth=2, rate=24000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(rate)
        wf.writeframes(frames)


# --- keying -----------------------------------------------------------------


def test_clip_path_is_deterministic_and_under_disclosure_dir(tmp_path):
    p = disclosure_clip_path(tmp_path, VOICE, TEXT)
    assert p == disclosure_clip_path(tmp_path, VOICE, TEXT)
    assert p.parent == tmp_path / "disclosure"
    assert p.suffix == ".wav" and len(p.stem) == 16
    int(p.stem, 16)  # hex


def test_clip_path_ignores_whitespace_and_unicode_form(tmp_path):
    base = disclosure_clip_path(tmp_path, VOICE, TEXT)
    nfd = unicodedata.normalize("NFD", TEXT)
    assert nfd != TEXT
    for variant in (TEXT + " ", "  " + TEXT + "\n", TEXT.replace(" ", "  "), TEXT.replace(" ", "\t"), nfd):
        assert disclosure_clip_path(tmp_path, VOICE, variant) == base


def test_clip_path_changes_with_voice(tmp_path):
    assert disclosure_clip_path(tmp_path, VOICE, TEXT) != disclosure_clip_path(tmp_path, "ef_dora", TEXT)
    assert disclosure_clip_path(tmp_path, VOICE, TEXT) == disclosure_clip_path(tmp_path, f" {VOICE}\n", TEXT)


def test_clip_path_changes_with_text(tmp_path):
    assert disclosure_clip_path(tmp_path, VOICE, TEXT) != disclosure_clip_path(tmp_path, VOICE, TEXT.rstrip("."))
    assert disclosure_clip_path(tmp_path, VOICE, TEXT) != disclosure_clip_path(tmp_path, VOICE, TEXT.lower())


# --- loading ----------------------------------------------------------------


def test_valid_clip_loads_as_tts_audio_frame(tmp_path):
    frames = _tone(2400, rate=24000)
    _write_wav(disclosure_clip_path(tmp_path, VOICE, TEXT), frames, rate=24000)

    clip = load_disclosure_clip(tmp_path, VOICE, TEXT + "  ")  # normalized lookup
    assert isinstance(clip, TTSAudioRawFrame) and isinstance(clip, OutputAudioRawFrame)
    assert clip.sample_rate == 24000
    assert clip.num_channels == 1
    assert clip.audio == frames
    assert clip.num_frames == 2400


def test_missing_clip_returns_none(tmp_path):
    assert load_disclosure_clip(tmp_path, VOICE, TEXT) is None


def test_stereo_clip_returns_none(tmp_path):
    _write_wav(disclosure_clip_path(tmp_path, VOICE, TEXT), _tone(200) * 2, channels=2)
    assert load_disclosure_clip(tmp_path, VOICE, TEXT) is None


def test_8bit_clip_returns_none(tmp_path):
    _write_wav(disclosure_clip_path(tmp_path, VOICE, TEXT), bytes(range(200)), sampwidth=1)
    assert load_disclosure_clip(tmp_path, VOICE, TEXT) is None


def test_empty_clip_returns_none(tmp_path):
    _write_wav(disclosure_clip_path(tmp_path, VOICE, TEXT), b"")
    assert load_disclosure_clip(tmp_path, VOICE, TEXT) is None


def test_corrupt_clip_returns_none(tmp_path):
    path = disclosure_clip_path(tmp_path, VOICE, TEXT)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a wav file at all" * 10)
    assert load_disclosure_clip(tmp_path, VOICE, TEXT) is None


def test_truncated_clip_returns_none(tmp_path):
    path = disclosure_clip_path(tmp_path, VOICE, TEXT)
    _write_wav(path, _tone(2400))
    data = path.read_bytes()
    path.write_bytes(data[: len(data) // 2])  # header still claims 2400 frames
    assert load_disclosure_clip(tmp_path, VOICE, TEXT) is None


# --- transport contract (pins pipecat 1.7 behaviour the design relies on) -----


class _RecordingOutput(BaseOutputTransport):
    """Output transport that accepts audio instantly and logs speaking events."""

    def __init__(self):
        super().__init__(TransportParams(audio_out_enabled=True))
        self.written: list[OutputAudioRawFrame] = []
        self.events: list[str] = []

    async def start(self, frame):
        await super().start(frame)
        await self.set_transport_ready(frame)  # what concrete transports do on connect

    async def write_audio_frame(self, frame):
        self.written.append(frame)
        return True

    async def process_frame(self, frame, direction):
        if isinstance(frame, EndFrame):
            self.events.append("EndFrame")
        await super().process_frame(frame, direction)

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        if direction == FrameDirection.DOWNSTREAM and isinstance(
            frame, (BotStartedSpeakingFrame, BotStoppedSpeakingFrame)
        ):
            self.events.append(type(frame).__name__)
        await super().push_frame(frame, direction)


async def _play(frames, before=()):
    out = _RecordingOutput()
    # 0.5 s settle is far below the transport's 3 s idle fallback, so a
    # BotStoppedSpeakingFrame before EndFrame can only come from TTSStoppedFrame.
    await run_test(
        Pipeline([*before, out]) if before else out,
        frames_to_send=[*frames, SleepFrame(sleep=0.5)],
        pipeline_params=PipelineParams(audio_out_sample_rate=8000),
    )
    return out


async def test_clip_plus_tts_stopped_completes_first_bot_turn(tmp_path):
    _write_wav(disclosure_clip_path(tmp_path, VOICE, TEXT), _tone(4800, rate=24000), rate=24000)
    clip = load_disclosure_clip(tmp_path, VOICE, TEXT)

    out = await _play([clip, TTSStoppedFrame()])

    # MuteUntilFirstBotCompleteUserMuteStrategy and DisclosureGate both key on this.
    assert out.events == ["BotStartedSpeakingFrame", "BotStoppedSpeakingFrame", "EndFrame"]
    # Transport resampled the 24 kHz clip to its 8 kHz output rate.
    assert out.written and all(f.sample_rate == 8000 for f in out.written)


async def test_plain_output_audio_frame_never_starts_bot_turn():
    # Why the loader returns TTSAudioRawFrame: a plain OutputAudioRawFrame plays
    # but emits no Bot*SpeakingFrame, so the user would stay muted.
    plain = OutputAudioRawFrame(audio=_tone(4800), sample_rate=24000, num_channels=1)
    out = await _play([plain, TTSStoppedFrame()])
    assert out.events == ["EndFrame"]
    assert out.written


# --- pipeline placement (QA C1: the clip must never reach STT) ----------------


class _FakeSTT(STTService):
    """Records every byte handed to run_stt — what Flux would receive."""

    def __init__(self):
        super().__init__()
        self.stt_bytes = 0

    async def run_stt(self, audio):
        self.stt_bytes += len(audio)
        yield None


class _Recorder(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.frames = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            self.frames.append(frame)
        await self.push_frame(frame, direction)


async def test_player_substitutes_clip_downstream_of_stt(tmp_path):
    audio = _tone(4800, rate=24000)  # 0.2 s -> 1600 samples / 3200 bytes at 8 kHz
    _write_wav(disclosure_clip_path(tmp_path, VOICE, TEXT), audio, rate=24000)
    stt = _FakeSTT()
    player = DisclosureClipPlayer(tmp_path, VOICE, TEXT)

    out = await _play(
        [TTSSpeakFrame(text=TEXT + " ", append_to_context=False)], before=(stt, player)
    )

    assert stt.stt_bytes == 0
    assert out.events == ["BotStartedSpeakingFrame", "BotStoppedSpeakingFrame", "EndFrame"]
    assert all(f.sample_rate == 8000 for f in out.written)
    # Clip chunks keep the TTSAudioRawFrame type; the transport's end-of-call
    # silence is a plain OutputAudioRawFrame. soxr's stream delay eats a few ms.
    clip_bytes = sum(len(f.audio) for f in out.written if isinstance(f, TTSAudioRawFrame))
    assert 0.75 * 3200 <= clip_bytes <= 3200 + out.audio_chunk_size


async def test_player_without_clip_passes_speak_frame_to_tts(tmp_path):
    stt = _FakeSTT()
    player = DisclosureClipPlayer(tmp_path, VOICE, TEXT)
    rec = _Recorder()
    speak = TTSSpeakFrame(text=TEXT, append_to_context=False)

    await run_test(Pipeline([stt, player, rec]), frames_to_send=[speak])

    assert stt.stt_bytes == 0
    assert [f for f in rec.frames if isinstance(f, TTSSpeakFrame)] == [speak]
    assert not [f for f in rec.frames if isinstance(f, OutputAudioRawFrame)]


async def test_player_only_replaces_the_first_disclosure(tmp_path):
    _write_wav(disclosure_clip_path(tmp_path, VOICE, TEXT), _tone(240), rate=24000)
    player = DisclosureClipPlayer(tmp_path, VOICE, TEXT)
    rec = _Recorder()
    other = TTSSpeakFrame(text="Hola, ¿en qué le puedo ayudar?")
    again = TTSSpeakFrame(text=TEXT)

    await run_test(
        Pipeline([player, rec]),
        frames_to_send=[other, TTSSpeakFrame(text=TEXT), again],
    )

    seen = [f for f in rec.frames if isinstance(f, (TTSSpeakFrame, TTSAudioRawFrame, TTSStoppedFrame))]
    assert [type(f).__name__ for f in seen] == [
        "TTSSpeakFrame", "TTSAudioRawFrame", "TTSStoppedFrame", "TTSSpeakFrame",
    ]
    assert seen[0] is other and seen[3] is again


async def test_player_ignores_upstream_disclosure(tmp_path):
    _write_wav(disclosure_clip_path(tmp_path, VOICE, TEXT), _tone(240), rate=24000)
    player = DisclosureClipPlayer(tmp_path, VOICE, TEXT)
    rec = _Recorder()
    speak = TTSSpeakFrame(text=TEXT)

    _, up = await run_test(
        Pipeline([player, rec]),
        frames_to_send=[speak],
        frames_to_send_direction=FrameDirection.UPSTREAM,
    )

    assert [f for f in up if isinstance(f, TTSSpeakFrame)] == [speak]  # forwarded as-is
    assert not [f for f in [*up, *rec.frames] if isinstance(f, OutputAudioRawFrame)]
