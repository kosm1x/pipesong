"""Pins where create_pipeline puts DisclosureClipPlayer (QA C1/W1) and the A.1 mixer.

A hand-built Pipeline in test_disclosure_clip.py cannot catch a misplaced or
deleted player in pipeline.py; this builds the real one offline. Needs the
kokoro-onnx model files in ~/.cache/pipecat/kokoro-onnx (KokoroTTSService
downloads them on first construction).
"""
from unittest.mock import MagicMock

from pipecat.processors.frame_processor import FrameProcessor

from pipesong.config import settings
from pipesong.pipeline import create_pipeline

TEXT = "Esta llamada está siendo grabada para fines de calidad y entrenamiento."


def _processor_names(disclosure_text):
    transport = MagicMock()
    transport.input.return_value = FrameProcessor(name="transport.input")
    transport.output.return_value = FrameProcessor(name="transport.output")
    task, _ = create_pipeline(
        transport=transport,
        system_prompt="x",
        language="es",
        voice="em_alex",
        disclosure_text=disclosure_text,
    )
    inner = [p for p in task._pipeline.processors if type(p).__name__ == "Pipeline"][0]
    return [type(p).__name__ for p in inner.processors]


def test_clip_player_sits_after_stt_and_right_before_tts():
    names = _processor_names(TEXT)
    player = names.index("DisclosureClipPlayer")
    assert player > names.index("DeepgramFluxSTTService")
    # Only the A.1 mixer may sit between the player and TTS.
    assert names[player + 1 : names.index("KokoroTTSService")] == ["PreRecordedMixer"]


def test_no_clip_player_without_disclosure_text():
    assert "DisclosureClipPlayer" not in _processor_names(None)


def test_mixer_sits_between_clip_player_and_tts_and_emitter_right_after_tts():
    names = _processor_names(TEXT)
    tts = names.index("KokoroTTSService")
    assert names[tts - 2 : tts + 2] == [
        "DisclosureClipPlayer", "PreRecordedMixer", "KokoroTTSService", "PreRecordedClipEmitter",
    ]
    assert names.index("PreRecordedMixer") > names.index("DeepgramFluxSTTService")
    assert names.count("PreRecordedMixer") == names.count("PreRecordedClipEmitter") == 1


def test_mixer_without_disclosure_text_still_right_before_tts():
    names = _processor_names(None)
    tts = names.index("KokoroTTSService")
    assert names[tts - 2 : tts + 2] == [
        "SentenceStreamBuffer", "PreRecordedMixer", "KokoroTTSService", "PreRecordedClipEmitter",
    ]


def test_mixer_flag_off_removes_mixer_and_emitter(monkeypatch):
    monkeypatch.setattr(settings, "prerecorded_mixer_enabled", False)
    names = _processor_names(TEXT)
    assert "PreRecordedMixer" not in names and "PreRecordedClipEmitter" not in names
    assert names[names.index("DisclosureClipPlayer") + 1] == "KokoroTTSService"
