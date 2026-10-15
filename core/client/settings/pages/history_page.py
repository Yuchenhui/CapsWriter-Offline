# coding: utf-8
"""历史页 (本地新增 2026-10-15): 列出每句最终上屏的文字 (最新在前), 一句一个复制按钮.
没打到目标位置的话, 从这里复制找回。页面数据来自根目录 history.jsonl (core/client/history.py)"""
import datetime as dt
import tkinter as tk

from core.client import history
from core.client.settings.widgets import Card, Button, heading, font

TITLE = '历史'
LIMIT = 200          # 页面最多显示的条数 (存储裁剪上限见 history.KEEP_LINES)


def build(parent, pal, ctx):
    f = tk.Frame(parent, bg=pal.bg)

    top = tk.Frame(f, bg=pal.bg)
    top.pack(fill='x', pady=(0, 16))
    heading(top, pal, '历史', '每句最终上屏的文字都会记在这里; 没打出来的话, 点「复制」找回').pack(side='left')

    lst = tk.Frame(f, bg=pal.bg)
    lst.pack(fill='both', expand=True)
    labels = []      # 正文 Label, 跟随列表宽度动态换行

    def copy_text(text: str, btn: Button) -> None:
        try:
            win = btn.winfo_toplevel()
            win.clipboard_clear()
            win.clipboard_append(text)
            btn.configure(text='已复制')
            btn.after(1200, lambda: btn.configure(text='复制'))
        except Exception:
            pass

    def reload() -> None:
        labels.clear()
        for w in lst.winfo_children():
            w.destroy()
        entries = history.read_entries(ctx.base, LIMIT)
        if not entries:
            tk.Label(lst, text='还没有记录', font=font(10), bg=pal.bg, fg=pal.muted).pack(anchor='w', pady=8)
            return
        for e in entries:
            c = Card(lst, pal, pad=12)
            c.pack(fill='x', pady=(0, 8))
            row = tk.Frame(c.body, bg=pal.surface)
            row.pack(fill='x')
            tstr = dt.datetime.fromtimestamp(e['ts']).strftime('%m-%d %H:%M:%S') if e['ts'] else ''
            tk.Label(row, text=tstr, font=font(9), bg=pal.surface, fg=pal.muted).pack(side='left')
            btn = Button(row, pal, '复制')
            btn.configure(font=font(9), padx=10, pady=2)
            btn.pack(side='right')
            # Button 的 command 是构造时闭包, 这里要引用按钮自身做反馈, 改为直接重绑点击
            btn.bind('<Button-1>', lambda _e, t=e['text'], b=btn: copy_text(t, b))
            lab = tk.Label(c.body, text=e['text'], font=font(10), bg=pal.surface, fg=pal.fg,
                           justify='left', anchor='w')
            lab.pack(fill='x', pady=(4, 0))
            labels.append(lab)

    def on_resize(event) -> None:
        w = max(200, event.width - 32)     # 减去卡片内边距
        for lab in labels:
            lab.configure(wraplength=w)

    lst.bind('<Configure>', on_resize)
    Button(top, pal, '刷新', command=reload, bg=pal.bg).pack(side='right')
    reload()
    return f
