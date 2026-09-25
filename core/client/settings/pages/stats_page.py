# coding: utf-8
import datetime as dt
import tkinter as tk

from core.client.settings import stats
from core.client.settings.widgets import Card, BarChart, heading, section, font
from core.tools.asr_usage import yuan
from core.tools.polish_usage import fmt

TITLE = '统计'


def build(parent, pal, ctx):
    f = tk.Frame(parent, bg=pal.bg)
    heading(f, pal, '统计', '识别 + 二次整理；按官方单价估算，实际以服务商账单为准').pack(anchor='w', pady=(0, 16))
    asr, pol, today = ctx.asr_usage(), ctx.polish_usage(), dt.date.today()

    row = tk.Frame(f, bg=pal.bg)
    row.pack(fill='x')
    for i, (name, s) in enumerate(stats.periods(asr, today, pol).items()):
        c = Card(row, pal)
        c.grid(row=0, column=i, sticky='nsew', padx=(0 if i == 0 else 12, 0))
        row.columnconfigure(i, weight=1, uniform='p')
        tk.Label(c.body, text=name, font=font(10), bg=pal.surface, fg=pal.muted).pack(anchor='w')
        tk.Label(c.body, text=yuan(s['yuan']), font=font(22, True), bg=pal.surface, fg=pal.fg).pack(anchor='w', pady=(2, 0))
        tk.Label(c.body, text=f"识别 {yuan(s['asr_yuan'])} · 整理 {yuan(s['polish_yuan'])}", font=font(10),
                 bg=pal.surface, fg=pal.fg).pack(anchor='w')
        tk.Label(c.body, text=f"{s['calls']} 句 · {s['sec'] / 60:.1f} 分钟", font=font(9),
                 bg=pal.surface, fg=pal.muted).pack(anchor='w', pady=(2, 0))

    month = today.isoformat()[:7]
    section(f, pal, '本月各模型').pack(anchor='w', pady=(22, 8))
    c = Card(f, pal)
    c.pack(fill='x')
    grid = c.body
    rows = [('识别', n, f'{calls} 句 · {m:.1f} 分钟', yuan(y)) for n, calls, m, y in stats.by_model(asr, month)]
    rows += [('整理', n, f'{calls} 次 · 上行 {fmt(i)} / 下行 {fmt(o)}', '订阅内' if y is None else yuan(y))
             for n, calls, i, o, y in stats.polish_by_provider(pol, month)]
    for j, h in enumerate(('类型', '模型', '用量', '花费')):
        tk.Label(grid, text=h, font=font(9), bg=pal.surface, fg=pal.muted, anchor='e' if j == 3 else 'w').grid(
            row=0, column=j, sticky='we', pady=(0, 6))
    for i, r in enumerate(rows or [('', '还没有记录', '', '')], start=1):
        for j, v in enumerate(r):
            fg = pal.muted if j in (0, 2) or v == '订阅内' else pal.fg
            tk.Label(grid, text=v, font=font(10), bg=pal.surface, fg=fg, anchor='e' if j == 3 else 'w').grid(
                row=i, column=j, sticky='we', pady=3, padx=(0, 12))
    for j, w in enumerate((1, 4, 4, 2)):
        grid.columnconfigure(j, weight=w)
    p = stats.polish_tokens(pol, month)
    if p['in_c']:
        tk.Label(c.body, text=f"二次整理上行缓存命中 {p['hit'] / p['in_c']:.0%}（命中部分按缓存价计）；MiniMax / MiMo 走订阅额度，不折算",
                 font=font(9), bg=pal.surface, fg=pal.muted).grid(row=len(rows) + 1, column=0, columnspan=4, sticky='w', pady=(8, 0))

    section(f, pal, '最近 14 天花费（识别 + 整理）').pack(anchor='w', pady=(22, 8))
    c = Card(f, pal)
    c.pack(fill='x')
    BarChart(c.body, pal, stats.daily(asr, today, polish=pol)).pack(fill='x')
    return f
