# coding: utf-8
"""统计页数据: 汇总 asr_usage.json (识别) 与 polish_usage.json (二次整理), 纯函数"""
import datetime as dt

from core.tools import asr_usage as au
from core.tools import polish_usage as pu

NAMES = {
    'qwen-audio-3.1-asr-flash-streaming': '千问 qwen-audio-3.1（流式）',
    'qwen3-asr-flash': '千问 qwen3-asr-flash',
    'asr-1.0': 'MiniMax asr-1.0',
    'mimo-v2.5-asr': '小米 mimo-v2.5-asr（套餐）',
}
POLISH_NAMES = {'deepseek': 'DeepSeek V4 Flash', 'minimax': 'MiniMax M3', 'mimo': 'MiMo V2.6 Flash', 'kimi': 'Kimi K2.8'}
LOCAL_NAMES = {'sensevoice': 'SenseVoice', 'fun_asr_nano': 'Fun-ASR-Nano', 'qwen_asr': 'Qwen3-ASR'}


def model_name(model: str) -> str:
    if model.startswith('local:'):
        return '本地 ' + LOCAL_NAMES.get(model[6:], model[6:])
    return NAMES.get(model, model)


def polish_period(polish: dict, prefix: str = '') -> dict:
    """二次整理: calls / yuan (按量部分) / plan_calls (订阅额度内的次数)"""
    s = {'calls': 0, 'yuan': 0.0, 'plan_calls': 0}
    for day, provs in polish.items():
        if day.startswith(prefix):
            for pid, e in provs.items():
                s['calls'] += e.get('calls', 0)
                y = pu.entry_cost(pid, e)
                if y is None:
                    s['plan_calls'] += e.get('calls', 0)
                else:
                    s['yuan'] += y
    return s


def periods(asr: dict, today: dt.date, polish: dict = None) -> dict:
    """{'今日': {...}, '本月': {...}, '累计': {...}}; 每项: 识别 calls / sec / asr_yuan, 整理 polish_calls / polish_yuan, 合计 yuan"""
    d, out = today.isoformat(), {}
    for name, pre in (('今日', d), ('本月', d[:7]), ('累计', '')):
        a, p = au.period(asr, pre), polish_period(polish or {}, pre)
        out[name] = {'calls': a['calls'], 'sec': a['sec'], 'asr_yuan': a['yuan'], 'polish_calls': p['calls'],
                     'polish_yuan': p['yuan'], 'yuan': a['yuan'] + p['yuan']}
    return out


def polish_by_provider(polish: dict, prefix: str = '') -> list:
    """[(显示名, 次数, 上行, 下行, 元 或 None=订阅内)]"""
    acc = {}
    for day, provs in polish.items():
        if day.startswith(prefix):
            for pid, e in provs.items():
                a = acc.setdefault(pid, {'calls': 0, 'in': 0, 'out': 0, 'yuan': None})
                a['calls'] += e.get('calls', 0); a['in'] += e.get('in', 0); a['out'] += e.get('out', 0)
                y = pu.entry_cost(pid, e)
                if y is not None:
                    a['yuan'] = (a['yuan'] or 0.0) + y
    rows = [(POLISH_NAMES.get(pid, pid), a['calls'], a['in'], a['out'], a['yuan']) for pid, a in acc.items()]
    return sorted(rows, key=lambda r: (-(r[4] or 0), -r[1]))


def by_model(asr: dict, prefix: str = '') -> list:
    """[(显示名, calls, 分钟, 元)] 按花费、句数降序"""
    acc = {}
    for day, models in asr.items():
        if not day.startswith(prefix):
            continue
        for m, e in models.items():
            a = acc.setdefault(m, [0, 0.0, 0.0])
            a[0] += e.get('calls', 0); a[1] += e.get('sec', 0); a[2] += au.cost(m, e)
    rows = [(model_name(m), c, s / 60, y) for m, (c, s, y) in acc.items()]
    return sorted(rows, key=lambda r: (-r[3], -r[1]))


def daily(asr: dict, today: dt.date, days: int = 14, polish: dict = None) -> list:
    """最近 days 天 [(月/日, 元)] (识别 + 整理), 旧 -> 新"""
    out = []
    for i in range(days - 1, -1, -1):
        d = today - dt.timedelta(days=i)
        out.append((f'{d.month}/{d.day}', au.period(asr, d.isoformat())['yuan'] + polish_period(polish or {}, d.isoformat())['yuan']))
    return out


def polish_tokens(polish: dict, prefix: str = '') -> dict:
    s = {'in': 0, 'out': 0, 'hit': 0, 'in_c': 0, 'calls': 0}
    for day, provs in polish.items():
        if day.startswith(prefix):
            for e in provs.values():
                for k in s:
                    s[k] += e.get(k, 0)
    return s


if __name__ == '__main__':
    today = dt.date(2026, 9, 25)
    asr = {'2026-09-25': {'qwen3-asr-flash': {'calls': 3, 'sec': 60.0, 'bsec': 60.0, 'in': 0, 'out': 0},
                          'local:qwen_asr': {'calls': 5, 'sec': 30.0, 'in': 0, 'out': 0}},
           '2026-09-20': {'asr-1.0': {'calls': 1, 'sec': 3600.0, 'bsec': 3600.0, 'in': 0, 'out': 0}},
           '2026-08-01': {'qwen3-asr-flash': {'calls': 1, 'sec': 10.0, 'bsec': 10.0, 'in': 0, 'out': 0}}}
    p = periods(asr, today)
    assert p['今日']['calls'] == 8 and abs(p['今日']['yuan'] - 60 * 0.00022) < 1e-12
    assert p['本月']['calls'] == 9 and p['累计']['calls'] == 10
    rows = by_model(asr, '2026-09')
    assert rows[0][0] == 'MiniMax asr-1.0' and abs(rows[0][3] - 2.5) < 1e-9 and rows[-1][0] == '本地 Qwen3-ASR'
    dl = daily(asr, today)
    assert len(dl) == 14 and dl[-1][0] == '9/25' and dl[-6][0] == '9/20' and abs(dl[-6][1] - 2.5) < 1e-9
    assert polish_tokens({'2026-09-25': {'deepseek': {'in': 10, 'out': 2, 'calls': 1}}})['in'] == 10
    pol = {'2026-09-25': {'deepseek': {'calls': 2, 'in': 3000, 'out': 50, 'hit': 2500, 'yuan': 0.002},
                          'mimo': {'calls': 4, 'in': 6000, 'out': 90}}}
    pp = periods(asr, today, pol)['今日']
    assert pp['polish_calls'] == 6 and abs(pp['polish_yuan'] - 0.002) < 1e-12 and abs(pp['yuan'] - (60 * 0.00022 + 0.002)) < 1e-12
    rows = polish_by_provider(pol)
    assert rows[0][0] == 'DeepSeek V4 Flash' and rows[1][0] == 'MiMo V2.6 Flash' and rows[1][4] is None   # 订阅内
    assert abs(daily(asr, today, polish=pol)[-1][1] - pp['yuan']) < 1e-12
    print('stats selftest ok')
