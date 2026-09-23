"""
麦克风校准 (托盘「麦克风校准…」): 依次读几句校准文本 -> 量音量/峰值/信噪比 + 识别字错率 -> 自动设当前麦克风的硬件增益.

校准期间: 识别结果不粘贴、不做二次整理 (量的是麦克风 + 识别的原始效果). 某句被判"没说话"就调高一档增益并请重读.
120 秒没有新结果自动取消, 避免一直吞掉正常的口述.
"""
import json
import logging
import re
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

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


class _Session:
    def __init__(self, app, dev_id, dev_name, gain_info, saved_polish):
        self.app, self.dev_id, self.dev_name = app, dev_id, dev_name
        self.gain0, self.mn, self.mx, self.step = gain_info
        self.gain = self.gain0
        self.saved_polish = saved_polish
        self.idx, self.rows, self.toast_id = 0, [], None
        self.deadline = time.time() + TIMEOUT_SEC


_s = None
_lock = threading.Lock()


def active() -> bool:
    return _s is not None


def _show(text: str) -> None:
    from core.ui.toast_manager import ToastMessageManager, ToastMessage
    m = ToastMessageManager()
    if _s.toast_id is None:
        _s.toast_id = m.add_message(ToastMessage(text=text, font_size=15, bg='#1E3A5F', fg='white', duration=30 * 60 * 1000,
                                                 initial_width=0.5, initial_height=0, streaming=False, window_type='text', markdown=False))
    else:
        m.update_toast(_s.toast_id, text)


def _close() -> None:
    """关掉校准提示窗口 (destroy 交给 Tk 线程执行)"""
    from core.ui.toast_manager import ToastMessageManager
    for w in list(ToastMessageManager().active_windows):
        if getattr(w, '_msg_id', None) == _s.toast_id:
            try:
                w.window.after(0, w._destroy_window)
            except Exception as e:
                logger.debug(f'关校准提示失败: {e}')


def _summary(text: str) -> None:
    from core.client.ui import toast
    toast(text, duration=20000, bg='#1B7F3B')


def _prompt() -> None:
    hint, sent = SENTENCES[_s.idx]
    _show(f'麦克风校准 {_s.idx + 1}/{len(SENTENCES)} · {_s.dev_name}\n按住右 Alt，{hint}：\n\n{sent}\n\n(校准期间不会粘贴; 当前增益 {_s.gain:+g} dB)')


def start(app) -> None:
    """托盘调用. 已在校准中则重新开始"""
    global _s
    from config_client import ClientConfig as Config
    from core.client.audio import mic_select
    from core.client.audio.default_device_watch import default_capture_id
    with _lock:
        devs = mic_select.list_capture()
        cur = default_capture_id()
        dev = next((d for d in devs if d[0] == cur), None)
        gi = mic_select.gain_info(cur) if dev else None
        if not dev or not gi or gi[0] is None:
            from core.client.ui import toast
            toast('读不到当前麦克风或它的增益, 无法校准', duration=3500)
            return
        saved = _s.saved_polish if _s else Config.polish
        _s = _Session(app, cur, dev[1], gi, saved)
        Config.polish = ''   # 量原始识别效果; 结束时恢复 (不写 user_state)
        logger.info(f'麦克风校准开始: {dev[1]}, 增益 {gi[0]:+g} dB')
        _prompt()
    threading.Thread(target=_watchdog, daemon=True, name='calib-timeout').start()


def _watchdog() -> None:
    while True:
        time.sleep(2)
        with _lock:
            if _s is None:
                return
            if time.time() > _s.deadline:
                logger.info('麦克风校准超时, 已取消')
                _close()
                _summary('麦克风校准: 120 秒没有新录音, 已取消 (增益未改)')
                _end()
                return


def _end() -> None:
    global _s
    from config_client import ClientConfig as Config
    Config.polish = _s.saved_polish
    _s = None


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
        hint, sent = SENTENCES[_s.idx]
        _show(f'没听到这句 (音量太小), 已把增益调到 {_s.gain:+g} dB, 请重读:\n\n{sent}')


def on_result(text: str) -> bool:
    """最终识别结果. 校准中: 记录并推进, 返回 True (调用方不要粘贴); 否则返回 False"""
    with _lock:
        if _s is None:
            return False
        lv = dict(getattr(_s.app.state, 'last_level', None) or {})
        ref = SENTENCES[_s.idx][1]
        _s.rows.append({'ref': ref, 'hyp': text, 'cer': round(cer(ref, text), 3), **lv})
        logger.info(f"校准 {_s.idx + 1}: 字错率 {cer(ref, text):.0%}, 峰值 {lv.get('peak', '?')}, 信噪比 {lv.get('snr', '?')} | {text}")
        _s.idx += 1
        _s.deadline = time.time() + TIMEOUT_SEC
        if _s.idx < len(SENTENCES):
            _prompt()
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
    lines = [f'麦克风校准完成 · {_s.dev_name}',
             f'增益: {_s.gain0:+g} dB -> {new if changed else _s.gain:+g} dB' + ('' if changed else ' (不用改)'),
             f'平均字错率: {avg_cer:.0%}   最低信噪比: {min(snrs):.0f} dB' if snrs else f'平均字错率: {avg_cer:.0%}',
             f'最大声那句峰值: {max(peaks):.1f} dBFS' if peaks else '']
    if quiet.get('avg') is not None and quiet['avg'] + (new - _s.gain if changed else 0) < QUIET_MIN_AVG:
        lines.append('最小声那句仍偏低: 麦克风再靠近嘴角一些')
    bad = [r for r in rows if r['cer'] > 0.15]
    if bad:
        lines.append('错得多的句子: ' + ' / '.join(r['hyp'][:18] for r in bad))
    _close()
    _summary('\n'.join(l for l in lines if l))
    try:
        with open(Path.cwd() / 'calibration_log.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps({'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'device': _s.dev_name, 'gain_before': _s.gain0,
                                'gain_after': new if changed else _s.gain, 'rows': rows}, ensure_ascii=False) + '\n')
    except OSError as e:
        logger.warning(f'写校准记录失败: {e}')
    logger.info('麦克风校准完成: ' + ' | '.join(l for l in lines if l))
    _end()


if __name__ == '__main__':   # 自检: 纯函数
    assert cer('今天天气不错。', '今天天气不错') == 0
    assert abs(cer('转译成英文', '转移成英文') - 0.2) < 1e-9
    assert cer('把 PostgreSQL 部署', '把postgresql部署') == 0
    # Jabra: 范围 -15..5 步长 2
    assert recommend_gain(-3, -15, 5, 2, -0.1) == -13      # 贴 0 削顶: -3 + (-6+0.1) - 4 = -12.9 -> 步长取整 -13
    assert recommend_gain(-7, -15, 5, 2, -6.2) == -7       # 已在目标附近, 不动
    assert recommend_gain(-9, -15, 5, 2, -20) == 5         # 太小: 夹到最大 +5
    assert recommend_gain(0, -17, 30, 0.031, -12) == 6.0 or abs(recommend_gain(0, -17, 30, 0.031, -12) - 6) < 0.05
    print('calibration selftest ok')
