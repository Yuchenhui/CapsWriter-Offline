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
        tmp = FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(tmp, FILE)


def fmt(n: int) -> str:
    return f'{n / 1e6:.1f}M' if n >= 1e6 else f'{n / 1e3:.1f}k' if n >= 1e3 else str(n)


def _sum(entries) -> tuple:
    """-> (token 总数, 调用次数)"""
    entries = list(entries)
    return sum(e['in'] + e['out'] for e in entries), sum(e['calls'] for e in entries)


def today(data: dict, pid: str = None) -> tuple:
    day = data.get(time.strftime('%Y-%m-%d'), {})
    return _sum(v for k, v in day.items() if pid is None or k == pid)


def total(data: dict) -> tuple:
    return _sum(v for day in data.values() for v in day.values())


if __name__ == '__main__':
    import tempfile
    FILE = Path(tempfile.mkdtemp()) / 'polish_usage.json'
    add('deepseek', {'prompt_tokens': 900, 'completion_tokens': 150})
    add('deepseek', {'prompt_tokens': 1000, 'completion_tokens': 50})
    add('minimax', {'prompt_tokens': 10, 'completion_tokens': 5})
    add('deepseek', None)                      # 响应没带 usage: 不记
    data = load()
    assert today(data) == (2115, 3), today(data)
    assert today(data, 'deepseek') == (2100, 2)
    assert total(data) == (2115, 3)
    assert (fmt(850), fmt(2115), fmt(1_234_567)) == ('850', '2.1k', '1.2M')
    print('polish_usage selftest ok')
