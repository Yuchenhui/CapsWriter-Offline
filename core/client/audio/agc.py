# coding: utf-8
"""小声说话自动增益 (2026-10-27): 气声说话时整句电平低 (实测说话平均 -46.8 dBFS),
云端引擎服务端 VAD 按绝对电平切, 只认出零星段 -> 返空或只出几个字 (实测 -53 dBFS 峰值的
58.6s 录音只回 14 字, 原电平回全文). 本模块按上一句测得的说话电平算一个恒定增益, 在送识别
前 (云端逐块 feed 与本地 AudioMessage) 把波形放大; 句内增益恒定, 句间更新, 存 state 单例
(AudioRecorder 每句新建, 实例属性活不过一句). 录音统计/校准仍按原始电平记录.
"""
from __future__ import annotations

import numpy as np


def gain_linear(voice_db: float | None, target_db: float, max_gain_db: float) -> float:
    """按说话电平 (dBFS) 算线性增益; 电平未知/异常 -> 1.0. 只放大不衰减, 上限 max_gain_db"""
    if voice_db is None or not np.isfinite(voice_db) or voice_db < -119.0:
        return 1.0
    gain_db = min(max(target_db - voice_db, 0.0), max(0.0, float(max_gain_db)))
    return 10.0 ** (gain_db / 20.0)


def apply(buf: np.ndarray, gain: float) -> np.ndarray:
    """float32 [-1,1] 波形乘增益后限幅回 [-1,1]. 必须保持 float32: 下游 BatchASR.feed 的 np.clip(-1,1)、
    AudioMessage 的 tobytes、Silero VAD 都按 float32 约定 —— 返回 int16 会被 clip 削成 ±1 方波 (2026-10-27 教训)"""
    if gain <= 1.001 or buf.size == 0:
        return buf
    return np.clip(buf.astype(np.float32) * float(gain), -1.0, 1.0)
