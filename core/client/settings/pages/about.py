# coding: utf-8
import os
import tkinter as tk
import webbrowser

from core.client.settings.widgets import Button, Card, heading, section, font

TITLE = '关于'
LINKS = (('我的 fork', 'https://github.com/Yuchenhui/CapsWriter-Offline'),
         ('上游原项目', 'https://github.com/HaujetZhao/CapsWriter-Offline'))
KEYS = (('千问 / 百炼', 'DASHSCOPE_API_KEY'), ('DeepSeek', 'DEEPSEEK_API_KEY'), ('MiniMax', 'MINIMAX_API_KEY'), ('MiMo', 'MIMO_API_KEY'),
        ('Kimi', 'KIMI_API_KEY'), ('智谱', 'ZHIPU_API_KEY'))


def build(parent, pal, ctx):
    from config_client import __version__
    from core.client.audio.cloud_asr import _has_key
    f = tk.Frame(parent, bg=pal.bg)
    heading(f, pal, 'CapsWriter Offline', f'版本 {__version__} · 本地改版（语音输入 · 按住右 Alt 说话）').pack(anchor='w', pady=(0, 16))

    c = Card(f, pal)
    c.pack(fill='x')
    for name, url in LINKS:
        row = tk.Frame(c.body, bg=pal.surface)
        row.pack(fill='x', pady=3)
        tk.Label(row, text=name, font=font(10), bg=pal.surface, fg=pal.muted, width=10, anchor='w').pack(side='left')
        link = tk.Label(row, text=url, font=font(10), bg=pal.surface, fg=pal.accent, cursor='hand2')
        link.pack(side='left')
        link.bind('<Button-1>', lambda e, u=url: webbrowser.open(u))

    section(f, pal, 'API key').pack(anchor='w', pady=(22, 8))
    c = Card(f, pal)
    c.pack(fill='x')
    for name, env in KEYS:
        ok = _has_key(env)
        row = tk.Frame(c.body, bg=pal.surface)
        row.pack(fill='x', pady=3)
        tk.Label(row, text=name, font=font(10), bg=pal.surface, fg=pal.fg, width=12, anchor='w').pack(side='left')
        tk.Label(row, text='已配置' if ok else f'未配置（环境变量 {env}）', font=font(10), bg=pal.surface,
                 fg=pal.accent if ok else pal.muted).pack(side='left')

    section(f, pal, '文件').pack(anchor='w', pady=(22, 8))
    row = tk.Frame(f, bg=pal.bg)
    row.pack(anchor='w')
    Button(row, pal, '打开安装目录', lambda: os.startfile(ctx.base), bg=pal.surface).pack(side='left', padx=(0, 10))
    Button(row, pal, '打开日志目录', lambda: os.startfile(ctx.base / 'logs'), bg=pal.surface).pack(side='left')
    return f
