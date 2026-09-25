# coding: utf-8
"""设置窗口自绘控件: 只用 Tk (Frame / Label / Canvas), 配色取 Palette"""
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

FONT = 'Microsoft YaHei UI'


def font(size: int, bold: bool = False):
    """size 按 pt 写 (设计稿习惯), 转成像素负字号: 不受根窗口 tk scaling (=2) 影响, 与预览一致"""
    px = -round(size * 96 / 72)
    return (FONT, px, 'bold') if bold else (FONT, px)


_SS = 4          # 抗锯齿超采样: Tk 画布画圆没有抗锯齿 (边缘一格格的), 圆点 / 开关用 PIL 4 倍画再缩小
_img_cache = {}


def _rgba(h: str, a: int = 255) -> tuple:
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


def _aa(key, w, h, draw):
    """缓存的抗锯齿图: draw(ImageDraw, k) 在 k 倍画布上画"""
    img = _img_cache.get(key)
    if img is None:
        big = Image.new('RGBA', (w * _SS, h * _SS), (0, 0, 0, 0))
        draw(ImageDraw.Draw(big), _SS)
        img = _img_cache[key] = ImageTk.PhotoImage(big.resize((w, h), Image.LANCZOS))
    return img


def radio_image(pal, on: bool, bg: str):
    def draw(d, k):
        d.ellipse((2 * k, 2 * k, 16 * k, 16 * k), fill=_rgba(bg), outline=_rgba(pal.accent if on else pal.border), width=2 * k)
        if on:
            d.ellipse((5.5 * k, 5.5 * k, 12.5 * k, 12.5 * k), fill=_rgba(pal.accent))
    return _aa(('radio', pal.accent, pal.border, bg, on), 18, 18, draw)


def toggle_image(pal, on: bool, bg: str):
    def draw(d, k):
        track = pal.accent if on else pal.border
        d.rounded_rectangle((1 * k, 1 * k, 41 * k, 23 * k), radius=11 * k, fill=_rgba(track))
        x = 30 if on else 12
        d.ellipse(((x - 9) * k, 3 * k, (x + 9) * k, 21 * k), fill=(255, 255, 255, 255))
    return _aa(('toggle', pal.accent, pal.border, bg, on), 42, 24, draw)


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
        self.dot = tk.Label(self, bg=pal.surface, bd=0)
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
        p = self.pal
        bg = p.hover if on else p.surface
        for w in self.winfo_children() + [self]:
            w.configure(bg=bg)
        self.dot.configure(image=radio_image(p, on, bg))

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

    def select(self, key):
        """只改显示, 不触发 on_pick (几组共用一个选中状态时互相同步用)"""
        for k, r in self.rows.items():
            r.set(k == key)

    def _pick(self, key):
        self.select(key)
        self.on_pick(key)


class Toggle(tk.Label):
    """开关. on_change(bool)"""

    def __init__(self, parent, pal, value=False, on_change=None, bg=None):
        super().__init__(parent, bg=bg or pal.surface, bd=0, cursor='hand2')
        self.pal, self.value, self.on_change, self.bg = pal, value, on_change, bg or pal.surface
        self.bind('<Button-1>', lambda e: self.set(not self.value, True))
        self._draw()

    def set(self, v, fire=False):
        self.value = v
        self._draw()
        if fire and self.on_change:
            self.on_change(v)

    def _draw(self):
        self.configure(image=toggle_image(self.pal, self.value, self.bg))


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


class Dropdown(tk.Frame):
    """下拉选择: 显示当前项 (标题 + 右侧说明 + ▾), 点开弹出列表. items: [{'key','title','detail','group','warn'}]; on_pick(key)"""

    def __init__(self, parent, pal, items, selected, on_pick):
        super().__init__(parent, bg=pal.surface, highlightthickness=1, highlightbackground=pal.border, cursor='hand2')
        self.pal, self.items, self.on_pick, self.key, self.pop = pal, items, on_pick, selected, None
        self.title = tk.Label(self, font=font(11), bg=pal.surface, fg=pal.fg, anchor='w')
        self.title.pack(side='left', padx=(12, 0), pady=9)
        tk.Label(self, text='▾', font=font(11), bg=pal.surface, fg=pal.muted).pack(side='right', padx=(6, 12))
        self.detail = tk.Label(self, font=font(10), bg=pal.surface, fg=pal.muted)
        self.detail.pack(side='right')
        for w in (self, *self.winfo_children()):
            w.bind('<Button-1>', lambda e: self.toggle())
        self.show(selected)

    def show(self, key):
        it = next((i for i in self.items if i['key'] == key), None)
        self.key = key
        self.title.configure(text=it['title'] if it else '')
        self.detail.configure(text=it.get('detail', '') if it else '')

    def toggle(self):
        if self.pop is not None:
            return self.close()
        p = self.pal
        self.pop = pop = tk.Toplevel(self)
        pop.overrideredirect(True)
        pop.attributes('-topmost', True)
        box = tk.Frame(pop, bg=p.surface, highlightthickness=1, highlightbackground=p.border)
        box.pack(fill='both', expand=True)
        group = None
        for it in self.items:
            if it.get('group') != group:
                group = it.get('group')
                tk.Label(box, text=group, font=font(9, True), bg=p.surface, fg=p.muted, anchor='w').pack(fill='x', padx=12, pady=(8, 2))
            on = it['key'] == self.key
            bg = p.hover if on else p.surface
            row = tk.Frame(box, bg=bg, cursor='hand2')
            row.pack(fill='x')
            tk.Label(row, text=it['title'], font=font(11), bg=bg, fg=p.fg, anchor='w').pack(side='left', padx=12, pady=6)
            if it.get('warn'):
                tk.Label(row, text=it['warn'], font=font(9), bg=bg, fg=p.danger).pack(side='left')
            tk.Label(row, text=it.get('detail', ''), font=font(10), bg=bg, fg=p.muted).pack(side='right', padx=12)
            for w in (row, *row.winfo_children()):
                w.bind('<Button-1>', lambda e, k=it['key']: self._pick(k))
                w.bind('<Enter>', lambda e, r=row, o=on: [c.configure(bg=p.hover) for c in (r, *r.winfo_children())])
                w.bind('<Leave>', lambda e, r=row, o=on: [c.configure(bg=p.hover if o else p.surface) for c in (r, *r.winfo_children())])
        self.update_idletasks()
        pop.geometry(f'{self.winfo_width()}x{box.winfo_reqheight()}+{self.winfo_rootx()}+{self.winfo_rooty() + self.winfo_height() + 2}')
        pop.bind('<FocusOut>', lambda e: self.after(80, self.close))
        pop.bind('<Escape>', lambda e: self.close())
        pop.focus_force()

    def close(self):
        if self.pop is not None:
            self.pop.destroy()
            self.pop = None

    def _pick(self, key):
        self.close()
        if key != self.key:
            self.show(key)
            self.on_pick(key)


class SortList(tk.Frame):
    """拖拽排序列表. items: [{'key','title','detail','local'}]; 本地模型行用另一种底色;
    第一个本地之后的行变淡 (轮不到). 松手后 on_change([key...])"""

    def __init__(self, parent, pal, items, on_change):
        super().__init__(parent, bg=pal.surface, highlightthickness=1, highlightbackground=pal.border)
        self.pal, self.on_change, self.items = pal, on_change, list(items)
        from core.client.settings.palette import mix
        self.local_bg = mix(pal.surface, pal.accent, 0.10)
        self.rows, self._drag = {}, None
        for it in self.items:
            self.rows[it['key']] = self._row(it)
        self._layout()

    def _row(self, it):
        p = self.pal
        bg = self.local_bg if it.get('local') else p.surface
        r = tk.Frame(self, bg=bg, cursor='fleur')
        r.num = tk.Label(r, font=font(10), bg=bg, fg=p.muted, width=2, anchor='e')
        r.num.pack(side='left', padx=(10, 6), pady=7)
        r.title = tk.Label(r, text=it['title'], font=font(11), bg=bg, fg=p.fg, anchor='w')
        r.title.pack(side='left')
        tk.Label(r, text='≡', font=font(12), bg=bg, fg=p.muted).pack(side='right', padx=(4, 12))
        r.detail = tk.Label(r, text=it.get('detail', ''), font=font(10), bg=bg, fg=p.muted)
        r.detail.pack(side='right')
        for w in (r, *r.winfo_children()):
            w.bind('<ButtonPress-1>', lambda e, k=it['key']: self._start(k))
            w.bind('<B1-Motion>', self._move)
            w.bind('<ButtonRelease-1>', self._end)
        return r

    def _layout(self):
        seen_local = False
        for w in self.pack_slaves():
            w.pack_forget()
        for i, it in enumerate(self.items):
            r = self.rows[it['key']]
            r.pack(fill='x')
            r.num.configure(text=str(i + 1))
            unused = seen_local                  # 第一个本地之后: 轮不到, 字变淡
            r.title.configure(fg=self.pal.muted if unused else self.pal.fg)
            seen_local = seen_local or it.get('local')

    def order(self) -> list:
        return [it['key'] for it in self.items]

    def _start(self, key):
        self._drag = key
        self.rows[key].configure(highlightthickness=1, highlightbackground=self.pal.accent)

    def _move(self, e):
        if self._drag is None:
            return
        y = e.y_root - self.winfo_rooty()
        tops = [self.rows[it['key']].winfo_y() + self.rows[it['key']].winfo_height() / 2 for it in self.items]
        idx = sum(1 for t in tops if y > t)
        cur = next(i for i, it in enumerate(self.items) if it['key'] == self._drag)
        idx = min(max(idx - (1 if idx > cur else 0), 0), len(self.items) - 1)
        if idx != cur:
            self.items.insert(idx, self.items.pop(cur))
            self._layout()

    def _end(self, _e):
        if self._drag is None:
            return
        self.rows[self._drag].configure(highlightthickness=0)
        self._drag = None
        self.on_change(self.order())
