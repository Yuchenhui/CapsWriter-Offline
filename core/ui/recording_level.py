# coding: utf-8
"""
录音电平总线

一个极简、线程安全、零项目依赖的共享通道：音频回调侧写入实时麦克风电平，
录音悬浮胶囊侧读取以驱动波形。带时间戳，读取方可判断数据是否新鲜——
不新鲜（如未录音、独立测试）时应回退到合成动画。

写入：core.client.audio.stream._audio_callback（每 ~50ms 一次）
读取：core.ui.toast_recording.ToastWindowRecording._tick（每帧）
"""

from __future__ import annotations

import time
import threading

_lock = threading.Lock()
_level = 0.0
_ts = 0.0


def set_level(level: float) -> None:
    """写入最新电平（0.0~1.0 量级的 RMS/峰值，未归一化也可）。"""
    global _level, _ts
    with _lock:
        _level = float(level)
        _ts = time.time()


def get_level(max_age: float = 0.25) -> tuple:
    """读取电平。

    Returns:
        (level, fresh)。fresh=False 表示超过 max_age 秒无更新，
        读取方应据此回退到合成动画。
    """
    with _lock:
        fresh = (time.time() - _ts) <= max_age
        return (_level if fresh else 0.0), fresh


def reset() -> None:
    """清零（在一次录音开始时调用，避免残留上次的电平）。"""
    global _level, _ts
    with _lock:
        _level = 0.0
        _ts = 0.0
