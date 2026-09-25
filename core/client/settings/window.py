# coding: utf-8
"""
设置窗口 (本地改 2026-09-25, 设计: docs/specs/2026-09-25-settings-window-design.md)

左侧导航 + 右侧可滚动内容; 配色跟随胶囊主题; Win11 标题栏跟随深浅色. 同一时刻只有一个窗口.
预览 (不接入客户端, 不改任何设置): python -m core.client.settings.window --preview [--shots 目录]
"""
import ctypes
import tkinter as tk

from core.client.settings import palette
from core.client.settings.pages import PAGES
from core.client.settings.widgets import font

_instance = None


def _dark_titlebar(win, dark: bool):
    """Win11 标题栏跟随深浅色. 须在窗口真正建好后设, 设完隐藏再显示一次才会重绘 (否则保持亮色)"""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        v = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(v), 4)   # DWMWA_USE_IMMERSIVE_DARK_MODE
        win.withdraw()
        win.deiconify()
    except Exception:
        pass


class ScrollArea(tk.Frame):
    """竖向滚动容器: 内容放 self.inner; 滚轮滚动 (鼠标在区域内时)"""

    def __init__(self, parent, bg):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor='nw')
        self.canvas.pack(fill='both', expand=True)
        self.inner.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.bind_all('<MouseWheel>', self._wheel, add='+')

    def _wheel(self, e):
        w = self.winfo_containing(e.x_root, e.y_root)
        while w is not None and w is not self:
            if isinstance(w, tk.Text):             # 编辑框自己滚
                return
            w = w.master
        if w is self and self.inner.winfo_height() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-e.delta / 120), 'units')

    def top(self):
        self.canvas.yview_moveto(0)


class SettingsWindow:
    def __init__(self, master, ctx):
        self.ctx = ctx
        self.pal = pal = palette.for_theme(ctx.state().get('capsule_theme', 'auto'))
        self.win = win = tk.Toplevel(master) if master is not None else tk.Tk()
        win.title('CapsWriter 设置')
        win.configure(bg=pal.bg)
        win.geometry('900x640')
        win.minsize(760, 520)
        _dark_titlebar(win, pal.dark)

        side = tk.Frame(win, bg=pal.side, width=200)
        side.pack(side='left', fill='y')
        side.pack_propagate(False)
        tk.Label(side, text='CapsWriter', font=font(14, True), bg=pal.side, fg=pal.fg).pack(anchor='w', padx=22, pady=(22, 2))
        tk.Label(side, text='语音输入 · 设置', font=font(9), bg=pal.side, fg=pal.muted).pack(anchor='w', padx=22, pady=(0, 18))
        tk.Frame(win, bg=pal.border, width=1).pack(side='left', fill='y')

        self.area = ScrollArea(win, pal.bg)
        self.area.pack(side='left', fill='both', expand=True)
        self.nav, self.page = {}, None
        for mod in PAGES:
            item = tk.Frame(side, bg=pal.side, cursor='hand2')
            item.pack(fill='x', padx=12, pady=1)
            bar = tk.Frame(item, bg=pal.side, width=3, height=18)
            bar.pack(side='left', padx=(0, 10))
            lab = tk.Label(item, text=mod.TITLE, font=font(11), bg=pal.side, fg=pal.muted, pady=8, anchor='w')
            lab.pack(side='left', fill='x', expand=True)
            for w in (item, lab, bar):
                w.bind('<Button-1>', lambda e, m=mod: self.show(m))
            self.nav[mod] = (item, bar, lab)
        self.show(PAGES[0])
        win.protocol('WM_DELETE_WINDOW', self.close)

    def show(self, mod):
        p = self.pal
        for m, (item, bar, lab) in self.nav.items():
            on = m is mod
            for w in (item, lab):
                w.configure(bg=p.hover if on else p.side)
            bar.configure(bg=p.accent if on else (p.hover if on else p.side))
            lab.configure(fg=p.fg if on else p.muted, font=font(11, on))
        if self.page is not None:
            self.page.destroy()
        try:
            self.page = mod.build(self.area.inner, p, self.ctx)
        except Exception as e:                        # 分页出错只显示在该页, 不影响窗口和语音输入
            self.page = tk.Label(self.area.inner, text=f'这一页出错了: {e}', bg=p.bg, fg=p.danger, font=font(10))
        self.page.pack(fill='both', expand=True, padx=32, pady=28)
        self.area.top()

    def close(self):
        global _instance
        _instance = None
        self.win.destroy()


def open_settings(master, ctx):
    """单实例: 已打开就提到最前 (须在 Tk 线程调用)"""
    global _instance
    if _instance is not None and _instance.win.winfo_exists():
        _instance.win.deiconify(); _instance.win.lift(); _instance.win.focus_force()
        return _instance
    _instance = SettingsWindow(master, ctx)
    return _instance


if __name__ == '__main__':
    import argparse, os
    ap = argparse.ArgumentParser()
    ap.add_argument('--preview', action='store_true')
    ap.add_argument('--base', default=r'C:\Users\Marshall\Apps\CapsWriter-Offline')
    ap.add_argument('--shots', default='')
    a = ap.parse_args()
    from core.client.settings.context import Context
    sw = open_settings(None, Context(a.base, readonly=True))
    if a.shots:                                         # 逐页截图后退出 (PrintWindow: 被别的窗口挡住也能截, 不抢前台)
        from PIL import Image
        win = sw.win

        def grab(hwnd):
            u, g = ctypes.windll.user32, ctypes.windll.gdi32
            r = ctypes.wintypes.RECT(); u.GetWindowRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            hdc = u.GetWindowDC(hwnd); mdc = g.CreateCompatibleDC(hdc); bmp = g.CreateCompatibleBitmap(hdc, w, h)
            g.SelectObject(mdc, bmp)
            u.PrintWindow(hwnd, mdc, 2)                  # PW_RENDERFULLCONTENT
            buf = ctypes.create_string_buffer(w * h * 4)
            bmi = (ctypes.c_uint32 * 10)(40, w, -h, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
            g.GetDIBits(mdc, bmp, 0, h, buf, bmi, 0)
            g.DeleteObject(bmp); g.DeleteDC(mdc); u.ReleaseDC(hwnd, hdc)
            return Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1).convert('RGB')

        def shoot(i=0):
            if i:
                win.update()
                hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
                grab(hwnd).save(os.path.join(a.shots, f'page{i}_{PAGES[i - 1].TITLE}.png'))
            if i < len(PAGES):
                sw.show(PAGES[i]); win.after(400, lambda: shoot(i + 1))
            else:
                win.destroy()
        import ctypes.wintypes
        win.after(600, shoot)
    sw.win.mainloop()
