# coding: utf-8
"""小声说话自动增益 (2026-10-27): 气声说话时整句电平低 (实测说话平均 -46.8 dBFS),
云端引擎常 VAD 判静音 -> 返空或只出结尾几个字. 本模块按上一句测得的说话电平算一个
恒定增益, 在送识别前 (云端逐块 feed 与本地 AudioMessage, 均为 16k int16) 把波形放大;
句内增益恒定避免中途跳变, 句间更新. 录音统计 (本句音量日志/校准) 仍按原始电平记录.
"""
from __future__ import annotations

import numpy as np

_CEIL = 31685   # 限幅上限 ≈ -0.3 dBFS, 留余量


def gain_linear(voice_db: float | None, target_db: float, max_gain_db: float) -> float:
    """按说话电平 (dBFS) 算线性增益; 电平未知/异常 -> 1.0. 只放大不衰减, 上限 max_gain_db"""
    if voice_db is None or not np.isfinite(voice_db) or voice_db < -119.0:
        return 1.0
    gain_db = min(max(target_db - voice_db, 0.0), max(0.0, float(max_gain_db)))
    return 10.0 ** (gain_db / 20.0)


def apply(buf: np.ndarray, gain: float) -> np.ndarray:
    """int16 波形乘增益; 块内峰值超限就整块按峰值回退 (限幅), 防止 int16 回绕爆音. 返回新数组"""
    if gain <= 1.001 or buf.size == 0:
        return buf
    out = buf.astype(np.float32) * float(gain)
    peak = float(np.max(np.abs(out)))
    if peak > _CEIL:
        out *= _CEIL / peak
    return out.astype(np.int16)
