# coding: utf-8
"""
二次整理 token 用量 (本地改 2026-09-25)

服务端每次调用接口后按 日期 x 服务商 累加进安装目录 polish_usage.json (被保险拦下用了原文的调用也算, token 已花);
客户端托盘读它显示. 两个进程只靠这个文件交流: 写入先写临时文件再原子替换, 读到半截不可能.
"""
import json
import os
import threading
import time
from pathlib import Path

FILE = Path(__file__).resolve().parents[2] / 'polish_usage.json'
_lock = threading.Lock()   # 超时的调用线程可能晚到, 与下一次调用并发写


def load() -> dict:
    try:
        return json.loads(FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def add(pid: str, usage: dict) -> None:
    """记一次调用; usage 是 OpenAI 兼容响应里的 usage 字段."""
    if not usage:
        return
    day = time.strftime('%Y-%m-%d')
    with _lock:
        data = load()
        d = data.setdefault(day, {}).setdefault(pid, {'calls': 0, 'in': 0, 'out': 0})
        d['calls'] += 1
        d['in'] += int(usage.get('prompt_tokens') or 0)
        d['out'] += int(usage.get('completion_tokens') or 0)
        # 上行里命中缓存的部分 (DeepSeek: prompt_cache_hit_tokens; OpenAI 兼容: prompt_tokens_details.cached_tokens)
        hit = usage.get('prompt_cache_hit_tokens') or (usage.get('prompt_tokens_details') or {}).get('cached_tokens')
        if hit is not None:   # in_c: 报了缓存字段的调用的上行, 算命中率的分母 (09-25 之前的旧记录没有, 不能拉低比例)
            d['hit'] = d.get('hit', 0) + int(hit)
            d['in_c'] = d.get('in_c', 0) + int(usage.get('prompt_tokens') or 0)
        p_in, p_out, p_hit = int(usage.get('prompt_tokens') or 0), int(usage.get('completion_tokens') or 0), int(hit or 0)
        y = call_cost(pid, p_in, p_hit, p_out, time.time())
        if y is not None:     # 按调用当时的时段计价后累加 (DeepSeek 分高峰 / 空闲, 按天汇总后就分不出了)
            d['yuan'] = round(d.get('yuan', 0.0) + y, 8)
        tmp = FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(tmp, FILE)


# 计费 (2026-09-25 查): DeepSeek 官方价 元/百万 token (缓存命中, 缓存未命中, 输出); 高峰 = 北京时间工作日 9-12, 14-18 点
# (法定节假日也算空闲, 这里不识别节假日, 节假日按高峰计 -> 宁可高估). MiniMax / MiMo 走订阅额度, 不折算成钱.
PRICES = {'deepseek': {'peak': (0.04, 2.0, 8.0), 'idle': (0.02, 1.0, 4.0)}}
PLAN = {'minimax', 'mimo', 'kimi'}


def _peak(ts: float) -> bool:
    bj = time.gmtime(ts + 8 * 3600)                    # 北京时间
    return bj.tm_wday < 5 and (9 <= bj.tm_hour < 12 or 14 <= bj.tm_hour < 18)


def call_cost(pid: str, tokens_in: int, hit: int, tokens_out: int, ts: float):
    """一次调用的钱 (元); 订阅 / 未知服务商返回 None"""
    p = PRICES.get(pid)
    if not p:
        return None
    h, m, o = p['peak' if _peak(ts) else 'idle']
    return (hit * h + (tokens_in - hit) * m + tokens_out * o) / 1e6


def entry_cost(pid: str, e: dict):
    """一条按天汇总记录的钱: 有 yuan 用 yuan; 旧记录 (09-25 之前没记 yuan) 按高峰价估算; 订阅返回 None"""
    if pid not in PRICES:
        return None
    if 'yuan' in e:
        return e['yuan']
    h, m, o = PRICES[pid]['peak']
    hit = e.get('hit', 0)
    return (hit * h + (e.get('in', 0) - hit) * m + e.get('out', 0) * o) / 1e6


def fmt(n: int) -> str:
    return f'{n / 1e6:.1f}M' if n >= 1e6 else f'{n / 1e3:.1f}k' if n >= 1e3 else str(n)


def _sum(entries) -> dict:
    """-> {'in': 上行, 'out': 下行, 'hit': 上行中命中缓存, 'calls': 次数} (上下行单价不同, 分开记; 09-25 之前的记录没有 hit)"""
    entries = list(entries)
    return {k: sum(e.get(k, 0) for e in entries) for k in ('in', 'out', 'hit', 'in_c', 'calls')}


def today(data: dict, pid: str = None) -> dict:
    day = data.get(time.strftime('%Y-%m-%d'), {})
    return _sum(v for k, v in day.items() if pid is None or k == pid)


def total(data: dict) -> dict:
    return _sum(v for day in data.values() for v in day.values())


def brief(s: dict) -> str:
    """'↑27.4k ↓469'"""
    return f"↑{fmt(s['in'])} ↓{fmt(s['out'])}"


def detail(s: dict) -> str:
    """'上行 27,372（缓存命中 87%） / 下行 469 / 17 次'"""
    cache = f"（缓存命中 {s['hit'] / s['in_c']:.0%}）" if s['in_c'] else ''
    return f"上行 {s['in']:,}{cache} / 下行 {s['out']:,} / {s['calls']} 次"


if __name__ == '__main__':
    import tempfile
    FILE = Path(tempfile.mkdtemp()) / 'polish_usage.json'
    add('deepseek', {'prompt_tokens': 900, 'completion_tokens': 150, 'prompt_cache_hit_tokens': 640})
    add('deepseek', {'prompt_tokens': 1000, 'completion_tokens': 50, 'prompt_tokens_details': {'cached_tokens': 640}})
    add('minimax', {'prompt_tokens': 10, 'completion_tokens': 5})
    add('deepseek', None)                      # 响应没带 usage: 不记
    data = load()
    assert today(data) == {'in': 1910, 'out': 205, 'hit': 1280, 'in_c': 1900, 'calls': 3}, today(data)
    assert today(data, 'deepseek') == {'in': 1900, 'out': 200, 'hit': 1280, 'in_c': 1900, 'calls': 2}
    assert total(data) == today(data)
    assert _sum([{'calls': 1, 'in': 5, 'out': 1}])['hit'] == 0   # 旧记录无 hit
    assert brief(today(data)) == '↑1.9k ↓205'
    assert detail(today(data)) == '上行 1,910（缓存命中 67%） / 下行 205 / 3 次', detail(today(data))   # 1280/1900, minimax 未报不进分母
    assert (fmt(850), fmt(2115), fmt(1_234_567)) == ('850', '2.1k', '1.2M')
    import calendar
    peak = calendar.timegm((2026, 9, 25, 2, 0, 0)) ; idle = calendar.timegm((2026, 9, 26, 2, 0, 0))   # 北京 周五 10 点 / 周六 10 点
    assert _peak(peak) and not _peak(idle)
    assert abs(call_cost('deepseek', 1476, 1280, 7, peak) - (1280 * 0.04 + 196 * 2 + 7 * 8) / 1e6) < 1e-15
    assert abs(call_cost('deepseek', 1476, 1280, 7, idle) * 2 - call_cost('deepseek', 1476, 1280, 7, peak)) < 1e-15
    assert call_cost('mimo', 100, 0, 10, peak) is None and entry_cost('minimax', {'in': 5}) is None
    assert data[time.strftime('%Y-%m-%d')]['deepseek'].get('yuan', 0) > 0   # add() 已按时段累加金额
    assert abs(entry_cost('deepseek', {'in': 1000, 'hit': 0, 'out': 100}) - (1000 * 2 + 100 * 8) / 1e6) < 1e-15   # 旧记录按高峰估
    print('polish_usage selftest ok')
