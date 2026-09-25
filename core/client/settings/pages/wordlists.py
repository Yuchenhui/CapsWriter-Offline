# coding: utf-8
import tkinter as tk

from core.client.settings.context import WORDLISTS
from core.client.settings.widgets import Button, heading, font

TITLE = '词库'
MONO = ('Cascadia Mono', 10)


def build(parent, pal, ctx):
    f = tk.Frame(parent, bg=pal.bg)
    heading(f, pal, '词库', '改完 Ctrl+S 或点「保存」，下一句就生效，不用重启').pack(anchor='w', pady=(0, 16))
    tabs = tk.Frame(f, bg=pal.bg)
    tabs.pack(anchor='w')
    desc = tk.Label(f, font=font(10), bg=pal.bg, fg=pal.muted, anchor='w')
    desc.pack(fill='x', pady=(10, 6))
    box = tk.Frame(f, bg=pal.surface, highlightthickness=1, highlightbackground=pal.border)
    box.pack(fill='both', expand=True)
    text = tk.Text(box, font=MONO, bg=pal.surface, fg=pal.fg, insertbackground=pal.fg, relief='flat', bd=0,
                   padx=12, pady=10, undo=True, height=20, wrap='none', selectbackground=pal.hover, selectforeground=pal.fg)
    sb = tk.Scrollbar(box, command=text.yview)
    text.configure(yscrollcommand=sb.set)
    sb.pack(side='right', fill='y')
    text.pack(side='left', fill='both', expand=True)
    bar = tk.Frame(f, bg=pal.bg)
    bar.pack(fill='x', pady=(10, 0))
    status = tk.Label(bar, font=font(10), bg=pal.bg, fg=pal.muted)
    status.pack(side='left')
    state = {'key': None, 'saved': ''}
    btns = {}

    def refresh_status(_e=None):
        body = text.get('1.0', 'end-1c')
        dirty = body != state['saved']
        n = sum(1 for l in body.splitlines() if l.strip() and not l.lstrip().startswith('#'))
        status.configure(text=f"{n} 条  ·  {'● 有未保存的改动' if dirty else '已保存'}", fg=pal.accent if dirty else pal.muted)

    def show(key):
        for k, b in btns.items():
            on = k == key
            b.configure(fg=pal.fg if on else pal.muted, font=font(11, on))
        _, _, fname, tip = next(w for w in WORDLISTS if w[0] == key)
        state['key'], state['file'] = key, fname
        desc.configure(text=f'{fname}  ·  {tip}')
        state['saved'] = ctx.read_wordlist(fname)
        text.delete('1.0', 'end')
        text.insert('1.0', state['saved'])
        text.edit_reset()
        refresh_status()

    def save(_e=None):
        body = text.get('1.0', 'end-1c')
        if ctx.do('save_wordlist', state['file'], len(body)):
            state['saved'] = body
            refresh_status()
        return 'break'

    for key, label, _, _ in WORDLISTS:
        b = tk.Label(tabs, text=label, bg=pal.bg, cursor='hand2', padx=2)
        b.pack(side='left', padx=(0, 22))
        b.bind('<Button-1>', lambda e, k=key: show(k))
        btns[key] = b
    Button(bar, pal, '保存', save, primary=True).pack(side='right')
    text.bind('<<Modified>>', lambda e: (refresh_status(), text.edit_modified(False)))
    text.bind('<Control-s>', save)
    show('terms')
    return f
