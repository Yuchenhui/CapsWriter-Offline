# coding: utf-8
import datetime as dt
import tkinter as tk

from core.client.settings import stats
from core.client.settings.widgets import Card, BarChart, heading, section, font
from core.tools.asr_usage import yuan

TITLE = '统计'


def build(parent, pal, ctx):
    f = tk.Frame(parent, bg=pal.bg)
    heading(f, pal, '统计', '在线识别按官方单价估算，实际以服务商账单为准').pack(anchor='w', pady=(0, 16))
    asr, today = ctx.asr_usage(), dt.date.today()

    row = tk.Frame(f, bg=pal.bg)
    row.pack(fill='x')
    for i, (name, s) in enumerate(stats.periods(asr, today).items()):
        c = Card(row, pal)
        c.grid(row=0, column=i, sticky='nsew', padx=(0 if i == 0 else 12, 0))
        row.columnconfigure(i, weight=1, uniform='p')
        tk.Label(c.body, text=name, font=font(10), bg=pal.surface, fg=pal.muted).pack(anchor='w')
        tk.Label(c.body, text=yuan(s['yuan']), font=font(22, True), bg=pal.surface, fg=pal.fg).pack(anchor='w', pady=(2, 0))
        tk.Label(c.body, text=f"{s['calls']} 句 · {s['sec'] / 60:.1f} 分钟", font=font(10), bg=pal.surface,
                 fg=pal.muted).pack(anchor='w')

    section(f, pal, '本月各模型').pack(anchor='w', pady=(22, 8))
    c = Card(f, pal)
    c.pack(fill='x')
    rows = stats.by_model(asr, today.isoformat()[:7])
    grid = c.body
    for j, h in enumerate(('模型', '句数', '时长', '花费')):
        tk.Label(grid, text=h, font=font(9), bg=pal.surface, fg=pal.muted, anchor='w' if j == 0 else 'e').grid(
            row=0, column=j, sticky='we', pady=(0, 6))
    for i, (name, calls, minutes, cost) in enumerate(rows or [('还没有记录', 0, 0, 0)], start=1):
        for j, v in enumerate((name, str(calls), f'{minutes:.1f} 分钟', yuan(cost))):
            tk.Label(grid, text=v, font=font(10), bg=pal.surface, fg=pal.fg, anchor='w' if j == 0 else 'e').grid(
                row=i, column=j, sticky='we', pady=3)
    grid.columnconfigure(0, weight=3)
    for j in (1, 2, 3):
        grid.columnconfigure(j, weight=1)

    section(f, pal, '最近 14 天花费').pack(anchor='w', pady=(22, 8))
    c = Card(f, pal)
    c.pack(fill='x')
    BarChart(c.body, pal, stats.daily(asr, today)).pack(fill='x')

    p = stats.polish_tokens(ctx.polish_usage(), today.isoformat()[:7])
    section(f, pal, '二次整理（本月）').pack(anchor='w', pady=(22, 8))
    c = Card(f, pal)
    c.pack(fill='x')
    hit = f"，缓存命中 {p['hit'] / p['in_c']:.0%}" if p['in_c'] else ''
    tk.Label(c.body, text=f"{p['calls']} 次 · 上行 {p['in']:,} / 下行 {p['out']:,} token{hit}", font=font(10),
             bg=pal.surface, fg=pal.fg).pack(anchor='w')
    return f
