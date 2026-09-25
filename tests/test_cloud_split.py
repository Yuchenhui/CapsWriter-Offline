# coding: utf-8
"""智谱 30 秒切段: 每段不超长, 拼回等于原音频, 刀口落在最安静处"""
import numpy as np

from core.client.audio.cloud_asr import _split

sr = 16000
x = np.full(int(65 * sr), 0.5, dtype=np.float32)
x[int(24 * sr):int(24.3 * sr)] = 0          # 24s 处一段静音
parts = _split(x)
assert all(len(p) <= 29 * sr for p in parts), [len(p) / sr for p in parts]
assert np.array_equal(np.concatenate(parts), x)
assert 24 * sr <= len(parts[0]) <= 24.3 * sr, len(parts[0]) / sr
assert len(_split(x[:10 * sr])) == 1
print('OK')
