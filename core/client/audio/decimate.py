"""
48 kHz -> 16 kHz 降采样 (先低通再抽取).

上游原写法 np.mean(data[::3], axis=1) 直接隔 3 取 1, 没有抗混叠滤波: 8 kHz 以上的成分 (齿音高频、环境高频噪声)
会折叠进 0-8 kHz 语音频段, 变成失真. 这里用 63 阶 Hamming 窗 sinc 低通 (截止 7.2 kHz) 后再抽取.
有状态: 跨 50 ms 数据块保留滤波尾巴和抽取相位, 否则每个块边界都会有咔哒声. 每段录音新建一个实例.
"""
import numpy as np

_TAPS = 63
_CUTOFF = 7200 / 48000                     # 归一化截止频率 (相对 48 kHz 采样率)
_n = np.arange(_TAPS) - (_TAPS - 1) / 2
_H = (2 * _CUTOFF * np.sinc(2 * _CUTOFF * _n) * np.hamming(_TAPS)).astype(np.float32)
_H /= _H.sum()                             # 直流增益 = 1, 音量不变


class Decimator3:
    def __init__(self):
        self._tail = np.zeros(_TAPS - 1, dtype=np.float32)
        self._phase = 0                    # 下一个要保留的样点在当前滤波输出里的偏移

    def process(self, block: np.ndarray) -> np.ndarray:
        """block: (n,) 或 (n, 声道) 的 48 kHz float32; 返回 16 kHz 单声道 float32"""
        x = block.mean(axis=1) if block.ndim == 2 else block
        buf = np.concatenate([self._tail, x.astype(np.float32, copy=False)])
        self._tail = buf[-(_TAPS - 1):]
        y = np.convolve(buf, _H, mode='valid')          # 长度 = len(x)
        out = y[self._phase::3]
        self._phase = (self._phase - len(y)) % 3
        return out.astype(np.float32, copy=False)


if __name__ == '__main__':   # 自检: 分块与整段一致 (无块边界伪影); 10 kHz 混叠被压住; 1 kHz 语音频段不衰减
    sr, t = 48000, np.arange(48000) / 48000
    speech = 0.3 * np.sin(2 * np.pi * 1000 * t).astype(np.float32)
    hiss = 0.3 * np.sin(2 * np.pi * 10000 * t).astype(np.float32)   # 10 kHz: 隔 3 取 1 会折叠成 6 kHz
    x = speech + hiss

    whole = Decimator3().process(x)
    d = Decimator3()
    chunked = np.concatenate([d.process(x[i:i + 2400]) for i in range(0, len(x), 2400)])
    assert len(whole) == len(chunked) == 16000 and np.allclose(whole, chunked, atol=1e-6), '分块结果应与整段一致'

    def level_at(sig, f, sr=16000):
        spec = np.abs(np.fft.rfft(sig[1000:])) / len(sig[1000:])
        return spec[int(round(f * len(sig[1000:]) / sr))]

    naive = x[::3]
    alias_naive, alias_new = level_at(naive, 6000), level_at(whole, 6000)
    keep_new, keep_naive = level_at(whole, 1000), level_at(naive, 1000)
    print(f'6 kHz 混叠分量: 原写法 {20*np.log10(alias_naive):.1f} dB -> 新写法 {20*np.log10(alias_new + 1e-12):.1f} dB')
    print(f'1 kHz 语音分量: 原写法 {20*np.log10(keep_naive):.1f} dB -> 新写法 {20*np.log10(keep_new):.1f} dB')
    assert alias_new < alias_naive / 100, '混叠应压低 40 dB 以上'
    assert abs(keep_new / keep_naive - 1) < 0.02, '语音频段增益应不变'
    print('decimate selftest ok')
