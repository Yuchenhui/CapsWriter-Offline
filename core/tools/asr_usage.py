# coding: utf-8
"""
在线识别用量与费用 (本地改 2026-09-25)

客户端每句话结束时, 把在线识别报的用量 (流式: 整段累计的上行/下行 token; 非流式: 接口返回的计费秒数)
和实际送出的音频秒数, 按 日期 x 模型 累加进安装目录 asr_usage.json
(客户端独占写; 服务端的二次整理用量在 polish_usage.json, 两进程不抢同一文件).
托盘「识别」读它显示今日 / 本月 / 累计的用量与金额.
"""
import json
import os
import threading
import time
from pathlib import Path

FILE = Path(__file__).resolve().parents[2] / 'asr_usage.json'
_lock = threading.Lock()

# 单价 (2026-09-25 查, 中国内地 / 华北2 北京):
#   ('token', 上行 元/百万token, 下行 元/百万token)   ('sec', 元/秒)
# qwen-audio-3.1-asr-flash-streaming: 百炼中文文档 上行 6 / 下行 4.5; 实测音频约 16~21 token/秒
# qwen3-asr-flash: 百炼中文文档 0.00022 元/秒 (音频时长)
# 套餐内不计钱 (不在表里 = 0): asr-1.0 (MiniMax Token Plan), mimo-v2.5-asr, glm-asr-2512
PRICES = {
    'qwen-audio-3.1-asr-flash-streaming': ('token', 6.0, 4.5),
    'qwen3-asr-flash': ('sec', 0.00022),
    'doubao-seed-asr-2.0': ('sec', 1 / 3600),        # 火山豆包流式 2.0 小时版 1 元/小时 (试用额度用完后)
}


def load() -> dict:
    try:
        return json.loads(FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def add(model: str, usage: dict, seconds: float) -> None:
    """记一句话. usage: 流式为最后一条结果的 payload.usage (累计值); 非流式为 {'seconds': 计费秒数}"""
    usage = usage or {}
    day = time.strftime('%Y-%m-%d')
    with _lock:
        data = load()
        d = data.setdefault(day, {}).setdefault(model, {'calls': 0, 'sec': 0.0, 'in': 0, 'out': 0})
        d['calls'] += 1
        d['sec'] = round(d['sec'] + seconds, 2)
        d['in'] += int(usage.get('input_tokens') or 0)
        d['out'] += int(usage.get('output_tokens') or 0)
        d['bsec'] = round(d.get('bsec', 0) + float(usage.get('seconds') or 0), 2)   # 接口报的计费秒数 (按秒计费的模型)
        tmp = FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(tmp, FILE)


def cost(model: str, e: dict) -> float:
    p = PRICES.get(model)
    if not p:
        return 0.0
    if p[0] == 'token':
        return (e.get('in', 0) * p[1] + e.get('out', 0) * p[2]) / 1e6
    return (e.get('bsec') or e.get('sec', 0)) * p[1]        # 按秒: 优先接口报的计费秒数


def _sum(items) -> dict:
    s = {'calls': 0, 'sec': 0.0, 'in': 0, 'out': 0, 'yuan': 0.0}
    for model, e in items:
        for k in ('calls', 'sec', 'in', 'out'):
            s[k] += e.get(k, 0)
        s['yuan'] += cost(model, e)
    return s


def period(data: dict, prefix: str = '') -> dict:
    """prefix: '' = 累计, '2026-09' = 本月, '2026-09-25' = 当天"""
    return _sum((m, e) for day, models in data.items() if day.startswith(prefix) for m, e in models.items())


def yuan(v: float) -> str:
    return f'¥{v:.2f}' if v >= 0.01 else ('¥0' if v == 0 else '<¥0.01')


def line(s: dict) -> str:
    """'音频 35.6 分钟 · 上行 45.2k / 下行 5.1k token · ¥0.29（243 句）'; 只有按秒计费的模型时不显示 token"""
    from core.tools.polish_usage import fmt
    tok = f" · 上行 {fmt(s['in'])} / 下行 {fmt(s['out'])} token" if s['in'] or s['out'] else ''
    return f"音频 {s['sec'] / 60:.1f} 分钟{tok} · {yuan(s['yuan'])}（{s['calls']} 句）"


if __name__ == '__main__':
    import tempfile
    FILE = Path(tempfile.mkdtemp()) / 'asr_usage.json'
    m = 'qwen-audio-3.1-asr-flash-streaming'
    add(m, {'input_tokens': 873, 'output_tokens': 99}, 55.0)
    add(m, {'input_tokens': 154, 'output_tokens': 22}, 7.29)
    add(m, None, 1.0)                                   # 没拿到 usage 也记一句 (音频秒数照记)
    add('qwen3-asr-flash', {'seconds': 55}, 55.0)
    add('asr-1.0', {'seconds': 55}, 55.0)
    today = time.strftime('%Y-%m-%d')
    s = period(load(), today)
    assert (s['calls'], s['in'], s['out'], round(s['sec'], 2)) == (5, 1027, 121, 173.29), s
    expect = (1027 * 6 + 121 * 4.5) / 1e6 + 55 * 0.00022           # asr-1.0 套餐内, 不计钱
    assert abs(s['yuan'] - expect) < 1e-12, (s['yuan'], expect)
    assert period(load(), time.strftime('%Y-%m')) == period(load()) == s
    assert yuan(0) == '¥0' and yuan(0.0067) == '<¥0.01' and yuan(0.29) == '¥0.29'
    assert 'token' not in line(_sum([('qwen3-asr-flash', load()[today]['qwen3-asr-flash'])]))
    print(line(s))
    print('asr_usage selftest ok')
