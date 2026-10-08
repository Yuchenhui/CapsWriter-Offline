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


def test_default_threshold_is_007():   # 本地改 2026-10-08: 阈值由 0.10 下调到 0.07, 放过 ratio 0.072-0.099 的边缘短句
    cfg_path = Path(__file__).resolve().parents[1] / 'config_client.py'
    spec = importlib.util.spec_from_file_location('_capswriter_cfg', cfg_path)
    cfg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cfg)
    assert cfg.ClientConfig.vad_max_speech_ratio == 0.07
