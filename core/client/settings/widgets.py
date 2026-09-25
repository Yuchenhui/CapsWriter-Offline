# coding: utf-8
"""设置窗口自绘控件: 只用 Tk (Frame / Label / Canvas), 配色取 Palette"""
import tkinter as tk

FONT = 'Microsoft YaHei UI'


def font(size: int, bold: bool = False):
    return (FONT, size, 'bold') if bold else (FONT, size)


class Card(tk.Frame):
    """卡片: 1px 描边 + 内边距"""

    def __init__(self, parent, pal, pad: int = 16, **kw):
        super().__init__(parent, bg=pal.surface, highlightthickness=1, highlightbackground=pal.border, **kw)
        self.body = tk.Frame(self, bg=pal.surface)
        self.body.pack(fill='both', expand=True, padx=pad, pady=pad - 4)


def heading(parent, pal, text: str, sub: str = ''):
    f = tk.Frame(parent, bg=pal.bg)
    tk.Label(f, text=text, font=font(18, True), bg=pal.bg, fg=pal.fg).pack(anchor='w')
    if sub:
        tk.Label(f, text=sub, font=font(10), bg=pal.bg, fg=pal.muted).pack(anchor='w', pady=(2, 0))
    return f


def section(parent, pal, text: str, bg=None):
    return tk.Label(parent, text=text, font=font(10, True), bg=bg or pal.bg, fg=pal.muted, anchor='w')


class Choice(tk.Frame):
    """单选行: 圆点 + 标题 + 右侧说明; 选中时底色高亮. on_pick(key)"""

    def __init__(self, parent, pal, key, title, detail='', selected=False, disabled=False, warn='', on_pick=None):
        super().__init__(parent, bg=pal.surface, cursor='' if disabled else 'hand2')
        self.pal, self.key, self.on_pick, self.disabled = pal, key, on_pick, disabled
        self.dot = tk.Canvas(self, width=18, height=18, bg=pal.surface, highlightthickness=0)
        self.dot.pack(side='left', padx=(10, 10), pady=10)
        fg = pal.muted if disabled else pal.fg
        self.title = tk.Label(self, text=title, font=font(11), bg=pal.surface, fg=fg, anchor='w')
        self.title.pack(side='left')
        if warn:
            self.warn = tk.Label(self, text=warn, font=font(9), bg=pal.surface, fg=pal.danger)
            self.warn.pack(side='left', padx=(8, 0))
        self.detail = tk.Label(self, text=detail, font=font(10), bg=pal.surface, fg=pal.muted)
        self.detail.pack(side='right', padx=12)
        for w in (self, self.dot, self.title, self.detail):
            w.bind('<Button-1>', self._click)
        self.set(selected)

    def set(self, on: bool):
        p, c = self.pal, self.dot
        bg = p.hover if on else p.surface
        for w in self.winfo_children() + [self]:
            w.configure(bg=bg)
        c.delete('all')
        c.create_oval(2, 2, 16, 16, outline=p.accent if on else p.border, width=2)
        if on:
            c.create_oval(6, 6, 12, 12, fill=p.accent, outline='')

    def _click(self, _e):
        if not self.disabled and self.on_pick:
            self.on_pick(self.key)


class ChoiceGroup(tk.Frame):
    """一组单选行 (卡片内), 行间细分隔线"""

    def __init__(self, parent, pal, items, selected, on_pick):
        super().__init__(parent, bg=pal.surface, highlightthickness=1, highlightbackground=pal.border)
        self.rows = {}
        for i, it in enumerate(items):
            if i:
                tk.Frame(self, bg=pal.border, height=1).pack(fill='x')
            r = Choice(self, pal, it['key'], it['title'], it.get('detail', ''), it['key'] == selected,
                       it.get('disabled', False), it.get('warn', ''), self._pick)
            r.pack(fill='x')
            self.rows[it['key']] = r
        self.on_pick = on_pick

    def _pick(self, key):
        for k, r in self.rows.items():
            r.set(k == key)
        self.on_pick(key)


class Toggle(tk.Canvas):
    """开关. on_change(bool)"""

    def __init__(self, parent, pal, value=False, on_change=None, bg=None):
        super().__init__(parent, width=42, height=24, bg=bg or pal.surface, highlightthickness=0, cursor='hand2')
        self.pal, self.value, self.on_change = pal, value, on_change
        self.bind('<Button-1>', lambda e: self.set(not self.value, True))
        self._draw()

    def set(self, v, fire=False):
        self.value = v
        self._draw()
        if fire and self.on_change:
            self.on_change(v)

    def _draw(self):
        p = self.pal
        self.delete('all')
        track = p.accent if self.value else p.border
        self.create_oval(1, 1, 23, 23, fill=track, outline='')
        self.create_oval(19, 1, 41, 23, fill=track, outline='')
        self.create_rectangle(12, 1, 30, 23, fill=track, outline='')
        x = 30 if self.value else 12
        self.create_oval(x - 9, 3, x + 9, 21, fill='#ffffff', outline='')


class Segmented(tk.Frame):
    """分段选择 (如 本地 / 云端). on_pick(key)"""

    def __init__(self, parent, pal, items, selected, on_pick):
        super().__init__(parent, bg=pal.border, padx=1, pady=1)
        self.pal, self.on_pick, self.btns = pal, on_pick, {}
        for key, text in items:
            b = tk.Label(self, text=text, font=font(11), padx=22, pady=6, cursor='hand2')
            b.pack(side='left', padx=(0, 1))
            b.bind('<Button-1>', lambda e, k=key: self.pick(k, True))
            self.btns[key] = b
        self.pick(selected)

    def pick(self, key, fire=False):
        p = self.pal
        for k, b in self.btns.items():
            on = k == key
            b.configure(bg=p.accent if on else p.surface, fg='#ffffff' if on and p.dark is False else (p.bg if on else p.fg))
        if fire:
            self.on_pick(key)


class Button(tk.Label):
    def __init__(self, parent, pal, text, command=None, primary=False, bg=None):
        super().__init__(parent, text=text, font=font(10, primary), padx=16, pady=6, cursor='hand2',
                         bg=pal.accent if primary else (bg or pal.hover), fg=(pal.bg if pal.dark else '#ffffff') if primary else pal.fg)
        self.bind('<Button-1>', lambda e: command and command())


class BarChart(tk.Canvas):
    """近 N 天柱状图: values [(标签, 数值)]"""

    def __init__(self, parent, pal, values, fmt=lambda v: f'¥{v:.2f}', height=150):
        super().__init__(parent, height=height, bg=pal.surface, highlightthickness=0)
        self.pal, self.values, self.fmt = pal, values, fmt
        self.bind('<Configure>', lambda e: self._draw())

    def _draw(self):
        p, vals = self.pal, self.values
        self.delete('all')
        w, h = self.winfo_width(), self.winfo_height()
        if not vals or w < 50:
            return
        top = max(v for _, v in vals) or 1
        n, gap, bottom = len(vals), 6, 22
        bw = max(4, (w - gap * (n - 1)) / n)
        for i, (label, v) in enumerate(vals):
            x0 = i * (bw + gap)
            bh = (h - bottom - 18) * v / top
            y0 = h - bottom - bh
            self.create_rectangle(x0, y0, x0 + bw, h - bottom, fill=p.accent if v else p.border, outline='')
            if v and v == top:
                self.create_text(x0 + bw / 2, y0 - 9, text=self.fmt(v), fill=p.fg, font=font(8))
            if i % 2 == 0 or n <= 7:
                self.create_text(x0 + bw / 2, h - 9, text=label, fill=p.muted, font=font(8))
