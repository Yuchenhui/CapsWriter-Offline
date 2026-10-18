"""
麦克风校准 (托盘 / 设置页「开始校准」): 依次读几句校准文本 -> 量音量/峰值/信噪比 + 识别字错率 -> 自动设麦克风的硬件增益.

校准绑的是**实际正在录的设备**, 不是 Windows 默认录音设备: 切换设备后默认端点要几秒才生效, 音频流也要等
default_device_watch 轮询后重开, 这中间两者会指向不同的麦克风 (2026-10-18 实测: 显示 Jabra, 实际录的是 ATK 无线麦).
校准期间自动切换麦克风 (mic_auto) 暂停, 免得录到一半被换设备、成绩和增益对不上; 中途仍被换掉就重头再来.

校准期间: 识别结果不粘贴、不做二次整理 (量的是麦克风 + 识别的原始效果). 某句被判"没说话"就调高一档增益并请重读.
120 秒没有新结果自动取消, 避免一直吞掉正常的口述. 界面在 core/client/ui/calib_window.py.
"""
import json
import logging
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger('client.' + __name__.rsplit('.', 1)[-1])   # 挂到 client 下才会写进 client_latest.log

# (提示, 要读的句子). 覆盖: 日常 / 英文术语 / 同音字 / 数字 / 最大声 (定峰值余量) / 最小声 (看是否被门限丢)
SENTENCES = [
    ('正常说', '今天天气不错，我们下午三点在会议室讨论一下新版本的发布计划。'),
    ('正常说', '把 PostgreSQL 和 Redis 部署到 Kubernetes 集群里，然后用 Claude Code 改一下配置。'),
    ('正常说', '这份文档需要转译成英文，另外把字符串里的反斜杠转义一下。'),
    ('正常说', '接口延迟从一点五秒降到了零点八秒，吞吐量提升了百分之四十。'),
    ('用你平时最大的音量说', '我觉得这个方案完全可行，明天就可以开始做！'),
    ('用你最小的音量说（别用气声）', '这件事我们明天再慢慢讨论吧。'),
]
TARGET_PEAK = -6.0     # 最响那句的峰值落在这里, 留 6 dB 余量不削顶
QUIET_MIN_AVG = -42.0  # 最小声那句平均低于它, 提示把麦克风靠近
TIMEOUT_SEC = 120

_PUNCT = re.compile(r'[\s，,。.；;：:、！!？?“”"\'（）()]')


def cer(ref: str, hyp: str) -> float:
    """字错率: 去标点空格、小写后的编辑距离 / 参考长度"""
    a, b = _PUNCT.sub('', ref).lower(), _PUNCT.sub('', hyp).lower()
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1] / max(len(a), 1)


def recommend_gain(cur_db: float, mn: float, mx: float, step: float, max_peak: float) -> float:
    """按最响那句的峰值推增益: 目标峰值 -6 dBFS; 峰值贴 0 说明已削顶, 真实峰值更高, 多降 4 dB. 结果按设备步长取整并夹在范围内"""
    delta = TARGET_PEAK - max_peak
    if max_peak > -1.0:
        delta -= 4.0
    target = cur_db + delta
    step = step if step and step > 0 else 1.0
    target = mn + round((target - mn) / step) * step
    return round(min(mx, max(mn, target)), 2)


def live_device(app, devs: list = None) -> tuple:
    """真正在录的麦克风, list_capture() 的一行 (id, 名称, 静音, 增益); 读不到返回 None.

    先信音频流报的设备名 (PortAudio 实际打开的那个), 读不到才退回 Windows 默认端点 —— 默认端点与实际录音
    会差几秒 (刚切设备 / 麦克风闲置释放后重开), 显示默认就可能显示成另一个设备.
    """
    from core.client.audio import mic_select
    from core.client.audio.default_device_watch import default_capture_id
    devs = mic_select.list_capture() if devs is None else devs
    name = ''
    try:
        name = app.stream.current_device_name()
    except Exception as e:
        logger.debug(f'读当前录音设备名失败: {e}')
    if name:
        dev = mic_select.resolve_capture(name, devs)
        if dev is not None:
            return dev
        logger.warning(f'当前录音设备 {name} 不在可用设备列表里, 退回按 Windows 默认设备判断')
    try:
        cur = default_capture_id()
    except Exception as e:
        logger.debug(f'读默认录音设备失败: {e}')
        return None
    return next((d for d in devs if d[0] == cur), None)


class _Session:
    def __init__(self, app, dev, gain_info, saved_polish, saved_auto):
        self.app, self.dev_id, self.dev_name = app, dev[0], dev[1]
        self.gain0, self.mn, self.mx, self.step = gain_info
        self.gain = self.gain0
        self.saved_polish, self.saved_auto = saved_polish, saved_auto
        self.idx, self.rows, self.note = 0, [], ''
        self.result = None      # 6 句跑完的成绩; None = 还在跑
        self.deadline = time.time() + TIMEOUT_SEC


_s = None
_lock = threading.Lock()
_note = ''    # 结束原因 (取消 / 超时), 窗口开着时显示


def active() -> bool:
    return _s is not None


def last_note() -> str:
    """上一次结束的原因; 下次 start() 清空"""
    return _note


def snapshot() -> dict:
    """给校准窗口的只读状态 (Tk 线程拿, 不碰 Tk)"""
    s = _s
    if s is None:
        return {'active': False}
    hint, sent = SENTENCES[min(s.idx, len(SENTENCES) - 1)]
    return {'active': True, 'done': s.result is not None, 'idx': s.idx, 'total': len(SENTENCES),
            'hint': hint, 'sentence': sent, 'dev_name': s.dev_name, 'gain': s.gain, 'gain0': s.gain0,
            'rows': [dict(r) for r in s.rows], 'note': s.note, 'result': s.result}


def start(app) -> bool:
    """托盘 / 设置页调用. 已在校准中 = 取消. 返回是否开了新会话"""
    global _s, _note
    from config_client import ClientConfig as Config
    from core.client.audio import mic_select
    _note = ''
    with _lock:
        if _s is not None:
            logger.info('麦克风校准已取消 (再次点击)')
            _note = '麦克风校准已结束 · 已取消 · 增益未改'
            _end()
            return False
        dev = live_device(app)
        gi = mic_select.gain_info(dev[0]) if dev else None
        if not dev or not gi or gi[0] is None:
            logger.warning('读不到当前麦克风或它的增益, 只能量音量不能调增益')
            _s = _Session(app, dev or ('', '未知设备', False, 0.0), (0.0, 0.0, 0.0, 1.0), Config.polish, Config.mic_auto)
            _s.note = '读不到当前麦克风的硬件增益, 这次只量音量, 不调增益'
            Config.polish = ''
        else:
            _s = _Session(app, dev, gi, Config.polish, Config.mic_auto)
            Config.polish = ''       # 量原始识别效果; 结束时恢复 (不写 user_state)
            if Config.mic_auto:      # 校准期间别让优先级切换把设备换掉 (手动选的设备本来也会被它切回)
                Config.mic_auto = False
                logger.info('麦克风校准开始: 暂停自动切换麦克风, 校准完恢复')
            logger.info(f'麦克风校准开始: {dev[1]}, 增益 {gi[0]:+g} dB')
    threading.Thread(target=_watchdog, daemon=True, name='calib-timeout').start()
    return True


def cancel(reason: str = '已取消') -> bool:
    """取消 (窗口关闭 / 托盘再点 / 超时). 返回是否真的取消了"""
    global _note
    with _lock:
        if _s is None:
            return False
        logger.info(f'麦克风校准已结束: {reason}')
        _note = f'麦克风校准已结束 · {reason} · 增益未改'
        _end()
        return True


def restore_gain() -> bool:
    """成绩页的「恢复校准前的增益」"""
    with _lock:
        if _s is None or _s.result is None:
            return False
        from core.client.audio import mic_select
        ok = mic_select.set_gain(_s.dev_id, _s.gain0)
        if ok:
            _s.gain, _s.result = _s.gain0, {**_s.result, 'gain_now': _s.gain0, 'applied': False,
                                            'undone': True}
            logger.info(f'麦克风校准: 增益恢复为 {_s.gain0:+g} dB')
        return ok


def _watchdog() -> None:
    while True:
        time.sleep(2)
        with _lock:
            if _s is None:
                return
            late = time.time() > _s.deadline
        if late:                       # cancel() 自己拿锁, 这里不能持着调
            cancel(f'{TIMEOUT_SEC} 秒没有新录音, 已自动取消')
            return


def _end() -> None:
    global _s
    from config_client import ClientConfig as Config
    Config.polish = _s.saved_polish
    if Config.mic_auto != _s.saved_auto:      # 校准期间暂停的自动切换, 这里恢复 (只改内存, 不写 user_state)
        Config.mic_auto = _s.saved_auto
        logger.info('麦克风校准结束: 恢复自动切换麦克风')
    _s = None


def _rebind() -> bool:
    """确认绑的还是正在录的那个设备. 换了就清空成绩重头来 (旧成绩的音量/增益对不上新设备)"""
    global _s
    from core.client.audio import mic_select
    dev = live_device(_s.app)
    if dev is None or dev[0] == _s.dev_id:
        return False
    gi = mic_select.gain_info(dev[0]) or (0.0, 0.0, 0.0, 1.0)
    logger.warning(f'校准期间录音设备换成 {dev[1]} (原来 {_s.dev_name}), 重新开始')
    _s.dev_id, _s.dev_name, _s.gain0, _s.gain = dev[0], dev[1], gi[0], gi[0]
    _s.mn, _s.mx, _s.step = gi[1], gi[2], gi[3]
    _s.idx, _s.rows, _s.result = 0, [], None
    _s.note = f'录音设备已换成 {dev[1]}, 重新开始校准'
    return True


def on_silence() -> None:
    """录音被判"没说话": 校准中则调高一档增益, 请重读"""
    from core.client.audio import mic_select
    with _lock:
        if _s is None:
            return
        _s.deadline = time.time() + TIMEOUT_SEC
        new = min(_s.mx, _s.gain + max(_s.step, 2.0))
        if new > _s.gain and mic_select.set_gain(_s.dev_id, new):
            _s.gain = new
        _s.note = f'没听到这句 (音量太小), 已把增益调到 {_s.gain:+g} dB, 请重读这句'


def on_result(text: str) -> bool:
    """最终识别结果. 校准中: 记录并推进, 返回 True (调用方不要粘贴); 否则返回 False"""
    with _lock:
        if _s is None:
            return False
        if _rebind():
            return True          # 这句是换设备前录的, 不计入新设备的成绩, 直接请用户重读第 1 句
        if _s.result is not None:              # 刚跑完 6 句, 又来一句迟到结果
            return True
        lv = dict(getattr(_s.app.state, 'last_level', None) or {})
        ref = SENTENCES[_s.idx][1]
        _s.rows.append({'ref': ref, 'hyp': text, 'cer': round(cer(ref, text), 3), **lv})
        logger.info(f"校准 {_s.idx + 1}: 字错率 {cer(ref, text):.0%}, 峰值 {lv.get('peak', '?')}, 信噪比 {lv.get('snr', '?')} | {text}")
        _s.idx += 1
        _s.deadline = time.time() + TIMEOUT_SEC
        if _s.idx < len(SENTENCES):
            _s.note = ''
        else:
            _finish()
        return True


def _finish() -> None:
    from core.client.audio import mic_select
    rows = _s.rows
    peaks = [r['peak'] for r in rows if 'peak' in r]
    quiet = rows[-1] if rows else {}
    avg_cer = sum(r['cer'] for r in rows) / len(rows)
    snrs = [r['snr'] for r in rows if 'snr' in r]
    new = recommend_gain(_s.gain, _s.mn, _s.mx, _s.step, max(peaks)) if peaks else _s.gain
    changed = new != _s.gain and mic_select.set_gain(_s.dev_id, new)
    now = new if changed else _s.gain
    tips = []
    if quiet.get('avg') is not None and quiet['avg'] + (now - _s.gain if changed else 0) < QUIET_MIN_AVG:
        tips.append('最小声那句仍偏低: 麦克风再靠近嘴角一些')
    bad = [r for r in rows if r['cer'] > 0.15]
    if bad:
        tips.append('错得多的句子: ' + ' / '.join(r['hyp'][:18] for r in bad))
    _s.result = {'device': _s.dev_name, 'gain_before': _s.gain0, 'gain_now': now, 'applied': bool(changed),
                 'avg_cer': avg_cer, 'min_snr': min(snrs) if snrs else None,
                 'max_peak': max(peaks) if peaks else None, 'tips': tips}
    _s.note = ''
    try:
        with open(Path.cwd() / 'calibration_log.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps({'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'device': _s.dev_name,
                                'gain_before': _s.gain0, 'gain_after': now, 'rows': rows}, ensure_ascii=False) + '\n')
    except OSError as e:
        logger.warning(f'写校准记录失败: {e}')
    logger.info(f'麦克风校准完成: {_s.dev_name} 增益 {_s.gain0:+g} -> {now:+g} dB, 平均字错率 {avg_cer:.0%}')


if __name__ == '__main__':   # 自检: 纯函数
    assert cer('今天天气不错。', '今天天气不错') == 0
    assert abs(cer('转译成英文', '转移成英文') - 0.2) < 1e-9
    assert cer('把 PostgreSQL 部署', '把postgresql部署') == 0
    # Jabra: 范围 -15..5 步长 2
    assert recommend_gain(-3, -15, 5, 2, -0.1) == -13      # 贴 0 削顶: -3 + (-6+0.1) - 4 = -12.9 -> 步长取整 -13
    assert recommend_gain(-7, -15, 5, 2, -6.2) == -7       # 已在目标附近, 不动
    assert recommend_gain(-9, -15, 5, 2, -20) == 5         # 太小: 夹到最大 +5
    assert recommend_gain(0, -17, 30, 0.031, -12) == 6.0 or abs(recommend_gain(0, -17, 30, 0.031, -12) - 6) < 0.05
    from core.client.audio.mic_select import name_matches, resolve_capture
    assert name_matches('Microphone (ATK N9V2 ULTIMATE d', 'Microphone (ATK N9V2 ULTIMATE dongle)')   # WASAPI 截断名
    assert not name_matches('Microphone (Jabra UC VOICE 250a)', 'Microphone (ATK N9V2 ULTIMATE dongle)')
    assert name_matches('Microphone (Jabra UC VOICE 250a) (2)', 'Microphone (Jabra UC VOICE 250a)')     # 重名后缀
    devs = [('id-a', 'Microphone (ATK N9V2 ULTIMATE dongle)', False, 2.0), ('id-b', 'Microphone (Jabra UC VOICE 250a)', False, 5.0)]
    assert resolve_capture('Microphone (ATK N9V2 ULTIMATE d', devs)[0] == 'id-a'
    assert resolve_capture('Microphone (Jabra UC VOICE 250a', devs)[0] == 'id-b'
    assert resolve_capture('Microphone (UUYC Virtual Audio Device)', devs) is None
    print('calibration selftest ok')
