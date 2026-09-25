# coding: utf-8
"""
在线识别用量与费用 (本地改 2026-09-25)

客户端每句话结束时, 把千问流式识别报的 usage (整段累计的上行/下行 token) 和实际送出的音频秒数,
按 日期 x 模型 累加进安装目录 asr_usage.json (客户端独占写; 服务端的二次整理用量在 polish_usage.json, 两进程不抢同一文件).
托盘「识别」读它显示今日 / 本月 / 累计的用量与金额.
"""
import json
import os
import threading
import time
from pathlib import Path

FILE = Path(__file__).resolve().parents[2] / 'asr_usage.json'
_lock = threading.Lock()

# 元 / 百万 token (上行, 下行). 出处: 阿里云百炼中文文档 qwen-audio-3-1-asr-flash-streaming, 华北2 (北京), 2026-09-25 查.
# 实测音频约 16~21 token/秒 (7.29s -> 154; 55s 里说话约 43s -> 873)
PRICES = {'qwen-audio-3.1-asr-flash-streaming': (6.0, 4.5)}


def load() -> dict:
    try:
        return json.loads(FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def add(model: str, usage: dict, seconds: float) -> None:
    """记一句话; usage: 最后一条结果里的 payload.usage (累计值)"""
    usage = usage or {}
    day = time.strftime('%Y-%m-%d')
    with _lock:
        data = load()
        d = data.setdefault(day, {}).setdefault(model, {'calls': 0, 'sec': 0.0, 'in': 0, 'out': 0})
        d['calls'] += 1
        d['sec'] = round(d['sec'] + seconds, 2)
        d['in'] += int(usage.get('input_tokens') or 0)
        d['out'] += int(usage.get('output_tokens') or 0)
        tmp = FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(tmp, FILE)


def _sum(items) -> dict:
    s = {'calls': 0, 'sec': 0.0, 'in': 0, 'out': 0, 'yuan': 0.0}
    for model, e in items:
        p_in, p_out = PRICES.get(model, (0.0, 0.0))
        for k in ('calls', 'sec', 'in', 'out'):
            s[k] += e.get(k, 0)
        s['yuan'] += (e.get('in', 0) * p_in + e.get('out', 0) * p_out) / 1e6
    return s


def period(data: dict, prefix: str = '') -> dict:
    """prefix: '' = 累计, '2026-09' = 本月, '2026-09-25' = 当天"""
    return _sum((m, e) for day, models in data.items() if day.startswith(prefix) for m, e in models.items())


def yuan(v: float) -> str:
    return f'¥{v:.2f}' if v >= 0.01 else ('¥0' if v == 0 else '<¥0.01')


def line(s: dict) -> str:
    """'音频 35.6 分钟 · 上行 45.2k / 下行 5.1k token · ¥0.29 (243 句)'"""
    from core.tools.polish_usage import fmt
    return f"音频 {s['sec'] / 60:.1f} 分钟 · 上行 {fmt(s['in'])} / 下行 {fmt(s['out'])} token · {yuan(s['yuan'])}（{s['calls']} 句）"


if __name__ == '__main__':
    import tempfile
    FILE = Path(tempfile.mkdtemp()) / 'asr_usage.json'
    m = 'qwen-audio-3.1-asr-flash-streaming'
    add(m, {'input_tokens': 873, 'output_tokens': 99}, 55.0)
    add(m, {'input_tokens': 154, 'output_tokens': 22}, 7.29)
    add(m, None, 1.0)                                   # 没拿到 usage 也记一句 (音频秒数照记)
    s = period(load(), time.strftime('%Y-%m-%d'))
    assert (s['calls'], s['in'], s['out'], round(s['sec'], 2)) == (3, 1027, 121, 63.29), s
    assert abs(s['yuan'] - (1027 * 6 + 121 * 4.5) / 1e6) < 1e-12
    assert period(load(), time.strftime('%Y-%m')) == period(load()) == s
    assert yuan(0) == '¥0' and yuan(0.0067) == '<¥0.01' and yuan(0.29) == '¥0.29'
    print(line(s))
    print('asr_usage selftest ok')
