"""Pins where create_pipeline puts DisclosureClipPlayer (QA C1/W1).

A hand-built Pipeline in test_disclosure_clip.py cannot catch a misplaced or
deleted player in pipeline.py; this builds the real one offline. Needs the
kokoro-onnx model files in ~/.cache/pipecat/kokoro-onnx (KokoroTTSService
downloads them on first construction).
"""
from unittest.mock import MagicMock

from pipecat.processors.frame_processor import FrameProcessor

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
    assert names[player + 1] == "KokoroTTSService"


def test_no_clip_player_without_disclosure_text():
    assert "DisclosureClipPlayer" not in _processor_names(None)
