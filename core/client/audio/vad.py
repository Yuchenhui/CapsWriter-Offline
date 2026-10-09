"""
Silero VAD 语音活动检测 (本地改 2026-10-03).

为什么需要: 敲桌子 / 按键盘是真实物理声音, 信噪比 (SNR) 判据天生拦不住. VAD 按语音概率区分
"人声" 与 "非语音噪声", 非语音的整句直接丢弃, 不送识别.

模型: silero-vad v5.1.2 ONNX (snakers4/silero-vad, MIT), 2.2 MB, CPU 单核毫秒级 (3 秒音频 ~8ms).
⚠️ 必须用 v5.x 模型: master (v6.x) 的 silero_vad.onnx 在 16 kHz 下实测全句概率 ~0.001 (2026-10-03 实测踩坑), v5.1.2 正常.
输入 16 kHz 单声道 float32, 512 样本一窗, 带内部 LSTM 状态.

判定口径: 统计语音概率 > 0.5 的窗, 累计"语音时长" (speech_sec) 与"语音占比" (ratio).
两个判据任一命中即判无人声: ① speech_sec < Config.vad_min_speech_sec (短的零星概率尖峰); ② ratio < Config.vad_max_speech_ratio
(长按里只夹了少量噪声尖峰, 如敲桌被误判成短促人声的 1s/13s=9%). 短句误杀风险: 正常一句话语音占比远高于 15%.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from . import logger

_SR = 16000
_WIN = 512          # silero 在 16k 下的窗口大小
_THRESH = 0.5       # 语音概率阈值 (官方默认)

_session = None     # 惰性创建的 onnxruntime InferenceSession
_state = None       # 跨句保留的 LSTM 状态? 不: 每句清零, 句间独立


def _model_path() -> Path:
    # 由源码位置确定安装根目录，不依赖启动目录。
    return Path(__file__).resolve().parents[3] / 'models' / 'silero_vad.onnx'


def _get_session():
    global _session
    if _session is None:
        import onnxruntime
        path = _model_path()
        if not path.exists():
            logger.warning(f'[vad] 模型不存在: {path}, VAD 判定将跳过 (放行)')
            return None
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 3
        _session = onnxruntime.InferenceSession(
            str(path), sess_options=opts, providers=['CPUExecutionProvider'])
        logger.info(f'[vad] Silero VAD 已加载: {path}')
    return _session


def speech_stats(pcm: np.ndarray) -> dict:
    """16 kHz 单声道整段音频 -> {'speech_sec': 语音时长秒, 'total_sec': 总时长, 'max_prob': 峰值概率, 'ratio': 语音占比}.
    接受 int16 (±32768) 或 float32 (±1, Decimator3 输出就是归一化 float) 两种刻度.
    模型缺失 / 音频过短时返回 speech_sec = None (调用方应放行, 不因 VAD 故障丢句子).
    """
    total_sec = len(pcm) / _SR
    if total_sec < 0.2:   # 太短没有判定意义, 放行交给上层判据
        return {'speech_sec': None, 'total_sec': round(total_sec, 2), 'max_prob': None, 'ratio': None}
    try:
        sess = _get_session()
        if sess is None:
            return {'speech_sec': None, 'total_sec': round(total_sec, 2), 'max_prob': None, 'ratio': None}
        x = pcm.astype(np.float32)
        if np.issubdtype(pcm.dtype, np.integer):
            x /= 32768.0                                   # int16 刻度 -> ±1
        elif float(np.max(np.abs(x))) > 1.5:               # float 但超出 ±1: 仍是 int16 刻度只是存成了 float
            x /= 32768.0
        # 否则视为已归一化 (±1), 直接用. 2026-10-03 踩坑: Decimator3 输出已是 float32 ±1,
        # 再除 32768 会变成 ~1e-5 的数字静音, VAD 恒判无人声 (峰值概率恒 0.044)
        # 峰值归一化到 0.9: 麦克风电平低的日子 (本机说话 p90 仅 -44 dBFS) 小声说话也能检出,
        # 实测衰减 40 dB 的语音归一化后照常识别, 噪声/敲桌脉冲归一化后仍判无人声
        _peak = float(np.max(np.abs(x))) or 1e-9
        x = x / _peak * 0.9
        state = np.zeros((2, 1, 128), dtype=np.float32)
        speech_frames = 0
        max_prob = 0.0
        for i in range(0, len(x) - _WIN + 1, _WIN):
            chunk = x[i:i + _WIN][None, :]     # (1, 512)
            prob, state = sess.run(
                None,
                {'input': chunk, 'state': state, 'sr': np.array(_SR, dtype=np.int64)},
            )
            p = float(prob[0][0])
            max_prob = max(max_prob, p)
            if p > _THRESH:
                speech_frames += 1
        speech_sec = speech_frames * _WIN / _SR
        return {'speech_sec': round(speech_sec, 2), 'total_sec': round(total_sec, 2),
                'max_prob': round(max_prob, 3),
                'ratio': round(speech_sec / total_sec, 3) if total_sec > 0 else 0.0}
    except Exception as e:
        logger.warning(f'[vad] VAD 加载或运行失败, 本句跳过 VAD: {type(e).__name__}: {e}')
        return {'speech_sec': None, 'total_sec': round(total_sec, 2), 'max_prob': None, 'ratio': None}
