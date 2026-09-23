"""
本句音量统计与"是不是根本没说话"的判断.

门限只看平均音量会误伤小声说话 (办公室压低嗓子, 平均可能低于 -45 dBFS). 真没说话时整段都是底噪,
最响的一成和最静的一成差不了几 dB; 小声说话音量虽低, 字和停顿的反差仍然大. 所以: 平均低于门限 **并且** 信噪比也低, 才判没说话.
"""
import numpy as np

SNR_MIN = 12.0   # dB; 低于它且平均音量低于门限 -> 判为没说话


def noise_voice(blocks_db: list) -> tuple:
    """每 50ms 块的电平 (dBFS) -> (底噪 = 10% 分位, 说话 = 90% 分位). 排除 < -90 的纯数字静音块 (麦克风重开的头几块)"""
    b = [x for x in blocks_db if x > -90]
    if not b:
        return -120.0, -120.0
    return float(np.percentile(b, 10)), float(np.percentile(b, 90))


def is_silence(avg_rms: float, blocks_db: list, gate: float, snr_min: float = SNR_MIN) -> bool:
    if not gate or avg_rms >= gate:
        return False
    noise, voice = noise_voice(blocks_db)
    return voice - noise < snr_min


if __name__ == '__main__':   # 自检
    rng = np.random.default_rng(0)
    gate = 0.0056   # -45 dBFS
    # 真没说话: 底噪 -60 上下 2 dB 抖动, 偶尔一下按键声
    silence = list(-60 + rng.normal(0, 2, 60)); silence[10] = -20
    # 小声说话: 字 -38 左右, 停顿 -62 左右, 平均 -48 (低于门限)
    quiet = [(-38 if i % 3 else -62) + rng.normal(0, 2) for i in range(60)]
    # 麦克风重开: 头几块纯数字静音 + 后面真没说话
    reopen = [-120] * 8 + list(-58 + rng.normal(0, 1.5, 40))
    avg = lambda db: 10 ** (np.mean(db) / 20)
    assert is_silence(avg(silence), silence, gate), '没说话应判静音'
    assert not is_silence(10 ** (-48 / 20), quiet, gate), '小声说话不该被丢'
    assert is_silence(10 ** (-60 / 20), reopen, gate), '重开的静音块不该把信噪比算高'
    assert not is_silence(10 ** (-30 / 20), silence, gate), '平均高于门限一律不丢'
    print('noise/voice 小声说话:', [round(x, 1) for x in noise_voice(quiet)], '没说话:', [round(x, 1) for x in noise_voice(silence)])
    print('level selftest ok')
