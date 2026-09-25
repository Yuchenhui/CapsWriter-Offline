# coding: utf-8
"""词库: 术语表 / 热词 / 替换规则, 一行一条; 底部添加, 每行 ✕ 删除; 改动立即保存, 下一句生效"""
import tkinter as tk

from core.client.settings import wordlist_model as wm
from core.client.settings.widgets import Button, heading, font

TITLE = '词库'
MONO = ('Cascadia Mono', -13)   # 像素字号, 不受 tk scaling 影响
TABS = (('terms', '术语表', 'terms.txt', '识别时提示模型的专有名词（千问非流式、本地识别、二次整理会参考）', False),
        ('hot', '热词', 'hot.txt', '识别结果读音相近时纠正成这些词；可写别名：词 | 别名；别名别用常见词的同音字（如"酷的"会把"库的"都改掉）', False),
        ('rule', '替换规则', 'hot-rule.txt', '把识别结果里"原来的"换成"换成的"，从上到下依次替换；支持正则；"换成的"留空 = 删除', True))


def _entry(parent, pal, width):
    return tk.Entry(parent, font=MONO, width=width, bg=pal.surface, fg=pal.fg, insertbackground=pal.fg, relief='flat',
                    highlightthickness=1, highlightbackground=pal.border, highlightcolor=pal.accent)


def build(parent, pal, ctx):
    f = tk.Frame(parent, bg=pal.bg)
    heading(f, pal, '词库', '添加、删除后立即保存，下一句就生效，不用重启').pack(anchor='w', pady=(0, 16))
    tabs = tk.Frame(f, bg=pal.bg)
    tabs.pack(anchor='w')
    desc = tk.Label(f, font=font(10), bg=pal.bg, fg=pal.muted, anchor='w', justify='left', wraplength=620)
    desc.pack(fill='x', pady=(10, 8))
    add = tk.Frame(f, bg=pal.bg)
    add.pack(fill='x')
    msg = tk.Label(f, font=font(9), bg=pal.bg, fg=pal.danger, anchor='w')
    msg.pack(fill='x', pady=(4, 8))
    box = tk.Frame(f, bg=pal.surface, highlightthickness=1, highlightbackground=pal.border)
    box.pack(fill='x')
    st = {'key': None}
    tab_btns = {}

    def save(data) -> bool:
        text = wm.format_rules(data) if st['rule'] else wm.format_items(data)
        if ctx.do('save_wordlist', st['file'], text):
            st['data'] = data
            return True
        msg.configure(text='保存失败，见日志')
        return False

    def render():
        for w in box.winfo_children():
            w.destroy()
        data = st['data']
        tab_btns[st['key']].configure(text=f"{st['label']} {len(data)}")
        if not data:
            tk.Label(box, text='还没有内容，在上面添加', font=font(10), bg=pal.surface, fg=pal.muted).pack(anchor='w', padx=14, pady=12)
        # 词表: 新加的在最上面; 替换规则: 按执行顺序 (从上到下依次替换), 新规则在最后
        order = range(len(data)) if st['rule'] else range(len(data) - 1, -1, -1)
        for n, i in enumerate(order):
            it = data[i]
            if n:
                tk.Frame(box, bg=pal.border, height=1).pack(fill='x')
            row = tk.Frame(box, bg=pal.surface)
            row.pack(fill='x')
            if st['rule']:
                pat, rep = it
                tk.Label(row, text=pat, font=MONO, bg=pal.surface, fg=pal.fg, anchor='w').pack(side='left', padx=(14, 0), pady=6)
                tk.Label(row, text='→', font=font(10), bg=pal.surface, fg=pal.muted).pack(side='left', padx=10)
                tk.Label(row, text=rep if rep else '（删除）', font=MONO, bg=pal.surface,
                         fg=pal.fg if rep else pal.muted, anchor='w').pack(side='left')
            else:
                tk.Label(row, text=it, font=MONO, bg=pal.surface, fg=pal.fg, anchor='w').pack(side='left', padx=14, pady=6)
            x = tk.Label(row, text='✕', font=font(10), bg=pal.surface, fg=pal.muted, cursor='hand2', padx=12)
            x.pack(side='right')
            x.bind('<Enter>', lambda e, w=x: w.configure(fg=pal.danger))
            x.bind('<Leave>', lambda e, w=x: w.configure(fg=pal.muted))
            x.bind('<Button-1>', lambda e, i=i: remove(i))

    def remove(i):
        if save(st['data'][:i] + st['data'][i + 1:]):
            msg.configure(text='')
            render()

    def do_add(_e=None):
        if st['rule']:
            pat, rep = st['e1'].get().strip(), st['e2'].get().strip()
            err = wm.rule_error(pat, rep)
            if not err and any(p == pat for p, _ in st['data']):
                err = '这条规则已经有了'
            new = st['data'] + [(pat, rep)]
        else:
            word = st['e1'].get().strip()
            if not word:
                return 'break'
            err = '已经有这一条了' if word in st['data'] else None
            new = st['data'] + [word]
        if err:
            msg.configure(text=err, fg=pal.danger)
            return 'break'
        if save(new):
            msg.configure(text='已加到最后一条（最后执行）' if st['rule'] else '', fg=pal.muted)
            for e in (st['e1'], st['e2']):
                if e is not None:
                    e.delete(0, 'end')
            st['e1'].focus_set()
            render()
        return 'break'

    def show(key):
        _, label, fname, tip, is_rule = next(t for t in TABS if t[0] == key)
        for k, b in tab_btns.items():
            on = k == key
            b.configure(fg=pal.fg if on else pal.muted, font=font(11, on))
        st.update(key=key, label=label, file=fname, rule=is_rule)
        desc.configure(text=tip)
        msg.configure(text='')
        text = ctx.read_wordlist(fname)
        st['data'] = wm.parse_rules(text) if is_rule else wm.parse_items(text)
        for w in add.winfo_children():
            w.destroy()
        st['e1'] = _entry(add, pal, 26 if is_rule else 42)
        st['e1'].pack(side='left', ipady=5)
        st['e2'] = None
        if is_rule:
            tk.Label(add, text='→', font=font(11), bg=pal.bg, fg=pal.muted).pack(side='left', padx=10)
            st['e2'] = _entry(add, pal, 16)
            st['e2'].pack(side='left', ipady=5)
            st['e2'].bind('<Return>', do_add)
        st['e1'].bind('<Return>', do_add)
        Button(add, pal, '＋ 添加', do_add, primary=True).pack(side='left', padx=(12, 0))
        render()

    for key, label, *_ in TABS:
        b = tk.Label(tabs, text=label, bg=pal.bg, cursor='hand2', padx=2)
        b.pack(side='left', padx=(0, 22))
        b.bind('<Button-1>', lambda e, k=key: show(k))
        tab_btns[key] = b
    show('terms')
    return f
