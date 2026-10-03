import importlib.util
from pathlib import Path


VOICE_GATE_PATH = Path(__file__).resolve().parents[1] / 'core/client/voice_gate.py'
spec = importlib.util.spec_from_file_location('_capswriter_voice_gate_regression', VOICE_GATE_PATH)
voice_gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(voice_gate)
vad_reasons = voice_gate.vad_reasons


def test_real_short_utterances_just_above_ten_percent_are_allowed():
    assert vad_reasons({'speech_sec': 0.64, 'ratio': 0.108}, 0.25, 0.10) == ()
    assert vad_reasons({'speech_sec': 0.48, 'ratio': 0.113}, 0.25, 0.10) == ()


def test_recorded_noise_sample_below_ten_percent_is_rejected():
    assert 'speech_ratio' in vad_reasons(
        {'speech_sec': 0.29, 'ratio': 0.091}, 0.25, 0.10
    )
