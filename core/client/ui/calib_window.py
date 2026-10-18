# coding: utf-8
"""
麦克风校准窗口 (本地改 2026-10-18)

独立窗口, 不再是逐句弹出的黑底文本框. 校准中显示: 正在录的设备 + 当前增益 + 进度 + 待读句 + 实时电平 +
上一句成绩; 6 句跑完换成成绩表 (字错率 / 峰值 / 信噪比) 和推荐增益. 状态全部来自 calibration.snapshot(),
这里只管画 (Tk 线程). 圆角和电平条走 widgets._aa 的抗锯齿图, 配色跟设置窗口同一套 (跟胶囊主题走).

预览 (不接客户端, 不碰麦克风): python -m core.client.ui.calib_window --preview [--shots 目录] [--theme frost]
"""
import math
import tkinter as tk

from core.client.settings.palette import for_theme
from core.client.settings.widgets import Button, Card, _aa, font

TICK_MS = 100          # 轮询校准状态 / 刷电平的间隔
WIN_W = 560


def _rgba(h: str, a: int = 255) -> tuple:
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


def _fmt(v, unit: str = '') -> str:
    return '-' if v is None else f'{v:g}{unit}'


def _db(v: float) -> float:
    return 20 * math.log10(max(v, 1e-6))


def _clip(text: str, measure, limit: int) -> str:
    """按真实字体度量截断 (不是按字数猜), 免得表格最后一列顶出卡片"""
    text = str(text)
    if measure(text) <= limit:
        return text
    out = ''
    for ch in text:
        if measure(out + ch + '…') > limit:
            break
        out += ch
    return (out + '…') if out else '…'


def _meter_image(pal, level: float, fresh: bool):
    """电平条: 圆角底槽 + 已填充部分. level 0~1, fresh=False (没在录音) 画成空槽"""
    w, h = 260, 10

    def draw(d, k):
        d.rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=h * k / 2, fill=_rgba(pal.surface))
        if level > 0.01:
            d.rounded_rectangle([0, 0, max(w * level, h) * k - 1, h * k - 1], radius=h * k / 2,
                                fill=_rgba(pal.accent if fresh else pal.border))
    return _aa(('meter', pal.surface, pal.accent, pal.border, round(level, 2), fresh), w, h, draw)


def _dot_image(pal, done: bool, cur: bool):
    """进度点: 已完成实心, 当前空心强调色, 未开始淡描边"""

    def draw(d, k):
        box = [0, 0, 11 * k - 1, 11 * k - 1]
        if done:
            d.ellipse(box, fill=_rgba(pal.accent))
        elif cur:
            d.ellipse(box, fill=_rgba(pal.accent))
            d.ellipse([3 * k, 3 * k, 8 * k - 1, 8 * k - 1], fill=_rgba(pal.bg))
        else:
            d.ellipse(box, outline=_rgba(pal.border), width=max(1, k))
    return _aa(('dot', pal.accent, pal.bg, pal.border, done, cur), 11, 11, draw)


class CalibWindow:
    """三种页面: 校准中 / 成绩 / 结束说明"""

    def __init__(self, master, app, pal):
        self.app, self.pal, self.phase = app, pal, ''
        self.sig, self.done_sig = None, None
        self.rows = []
        self._closed = False
        self._after = None
        self.win = win = tk.Toplevel(master)
        win.title('麦克风校准')
        win.geometry(f'{WIN_W}x540')        # 成绩页要放 6 行表, 切页时再拉高 (见 _build_done)
        win.minsize(WIN_W - 40, 480)
        win.configure(bg=pal.bg)
        win.protocol('WM_DELETE_WINDOW', self.close)
        self.body = tk.Frame(win, bg=pal.bg)
        self.body.pack(fill='both', expand=True, padx=28, pady=24)
        self._build_run()
        self._after = win.after(TICK_MS, self._tick)

    def _clear(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()

    # ---- 校准页 ----
    def _build_run(self) -> None:
        p, b = self.pal, self.body
        top = tk.Frame(b, bg=p.bg)
        top.pack(fill='x')
        tk.Label(top, text='麦克风校准', font=font(16, True), bg=p.bg, fg=p.fg).pack(side='left')
        self.lb_step = tk.Label(top, text='', font=font(11), bg=p.bg, fg=p.muted)
        self.lb_step.pack(side='right')

        self.lb_dev = tk.Label(b, text='', font=font(12, True), bg=p.bg, fg=p.fg, anchor='w', justify='left')
        self.lb_dev.pack(fill='x', pady=(16, 0))
        self.lb_note = tk.Label(b, text='', font=font(10), bg=p.bg, fg=p.accent, anchor='w', justify='left',
                                wraplength=WIN_W - 60)
        self.lb_note.pack(fill='x', pady=(6, 0))

        dots = tk.Frame(b, bg=p.bg)
        dots.pack(fill='x', pady=(14, 0))
        self.dots = [tk.Label(dots, bg=p.bg, width=16) for _ in range(6)]
        for d in self.dots:
            d.pack(side='left')

        card = Card(b, p)
        card.pack(fill='x', pady=(12, 0))
        self.lb_hint = tk.Label(card.body, text='', font=font(10), bg=p.surface, fg=p.muted, anchor='w')
        self.lb_hint.pack(fill='x')
        self.lb_sent = tk.Label(card.body, text='', font=font(14), bg=p.surface, fg=p.fg,
                                anchor='w', justify='left', wraplength=WIN_W - 100)
        self.lb_sent.pack(fill='x', pady=(8, 0))

        meter = tk.Frame(b, bg=p.bg)
        meter.pack(fill='x', pady=(18, 0))
        self.lb_meter = tk.Label(meter, bg=p.bg, width=260, height=10)
        self.lb_meter.pack(side='left')
        self.lb_gain = tk.Label(meter, text='', font=font(11, True), bg=p.bg, fg=p.fg)
        self.lb_gain.pack(side='right')
        self.lb_db = tk.Label(meter, text='', font=font(10), bg=p.bg, fg=p.muted)
        self.lb_db.pack(side='left', padx=(10, 0))

        self.lb_last = tk.Label(b, text='', font=font(10), bg=p.bg, fg=p.muted, anchor='w', justify='left',
                                wraplength=WIN_W - 60)
        self.lb_last.pack(fill='x', pady=(14, 0))

        tk.Label(b, text='按住右 Alt 说话, 松开后自动记这一句; 校准期间说的话不会粘贴到光标处', font=font(9),
                 bg=p.bg, fg=p.muted, anchor='w', wraplength=WIN_W - 60, justify='left').pack(fill='x', pady=(12, 0))
        btns = tk.Frame(b, bg=p.bg)
        btns.pack(fill='x', side='bottom', pady=(10, 0))
        Button(btns, p, '取消', self.cancel).pack(side='right')
        Button(btns, p, '重新开始', self.restart).pack(side='right', padx=(0, 8))

    # ---- 成绩页 ----
    def _build_done(self, res: dict, rows: list) -> None:
        p, b = self.pal, self.body
        self._clear()
        self.win.geometry(f'{WIN_W}x600')      # 成绩表 6 行 + 提示 + 按钮, 比校准页高
        tk.Label(b, text='校准完成', font=font(16, True), bg=p.bg, fg=p.fg).pack(anchor='w')
        tk.Label(b, text=res['device'], font=font(10), bg=p.bg, fg=p.muted, anchor='w').pack(anchor='w', pady=(4, 0))
        if res.get('undone'):
            gain_text, gain_fg = f'增益已恢复为校准前的 {_fmt(res["gain_now"])} dB', p.muted
        elif res.get('applied'):
            gain_text, gain_fg = f'增益 {_fmt(res["gain_before"])} → {_fmt(res["gain_now"])} dB（已自动设）', p.accent
        else:
            gain_text, gain_fg = f'增益维持 {_fmt(res["gain_now"])} dB, 不用改', p.muted
        tk.Label(b, text=gain_text, font=font(13, True), bg=p.bg, fg=gain_fg).pack(anchor='w', pady=(14, 0))
        stats = f"平均字错率 {res['avg_cer']:.0%}"
        if res.get('min_snr') is not None:
            stats += f"　最低信噪比 {res['min_snr']:.0f} dB"
        if res.get('max_peak') is not None:
            stats += f"　最大声峰值 {res['max_peak']:.1f} dBFS"
        tk.Label(b, text=stats, font=font(10), bg=p.bg, fg=p.muted, anchor='w').pack(anchor='w', pady=(6, 0))

        btns = tk.Frame(b, bg=p.bg)          # 先占底部, 再让成绩表吃掉剩余高度 (否则表格会把按钮顶出窗口)
        btns.pack(fill='x', side='bottom', pady=(14, 0))
        if res.get('applied') and not res.get('undone'):
            Button(btns, p, f'恢复校准前的 {_fmt(res["gain_before"])} dB', self._undo).pack(side='left')
        Button(btns, p, '再校准一次', self.restart).pack(side='right')
        Button(btns, p, '完成', self.close, primary=True).pack(side='right', padx=(0, 8))
        for tip in res.get('tips', []):
            tk.Label(b, text='· ' + tip, font=font(10), bg=p.bg, fg=p.muted, anchor='w',
                     wraplength=WIN_W - 60).pack(anchor='w', pady=(8, 0))
        table = Card(b, p)
        table.pack(fill='both', expand=True)
        grid = table.body
        widths = (30, 60, 58, 64)          # 前四列固定, 最后一列吃掉剩余宽度
        import tkinter.font as tkfont
        measure = tkfont.Font(font=font(10)).measure
        limit = WIN_W - 56 - 36 - sum(widths) - 12     # 窗口 - 边距 - 卡片内边距 - 四个定宽列 - 余量
        grid.grid_columnconfigure(4, weight=1)
        for col, (text, w) in enumerate(zip(('#', '字错率', '峰值', '信噪比', '识别结果'), widths + (0,))):
            tk.Label(grid, text=text, font=font(9), bg=p.surface, fg=p.muted,
                     width=max(1, round(w / 8)) if w else 0, anchor='center' if col < 4 else 'w'
                     ).grid(row=0, column=col, pady=(0, 8), sticky='w' if col < 4 else 'ew')
        for i, row in enumerate(rows, start=1):
            c = row.get('cer', 0)
            cells = [(str(i), p.muted), (f'{c:.0%}', p.danger if c > 0.15 else p.fg),
                     (f"{row['peak']:.1f}" if row.get('peak') is not None else '-', p.fg),
                     (f"{row['snr']:.0f} dB" if row.get('snr') is not None else '-', p.fg)]
            for col, (text, color) in enumerate(cells):
                tk.Label(grid, text=text, font=font(10), bg=p.surface, fg=color,
                         width=max(1, round(widths[col] / 8)), anchor='center').grid(row=i, column=col, pady=4)
            tk.Label(grid, text=_clip(row.get('hyp', ''), measure, limit), font=font(10), bg=p.surface,
                     fg=p.fg, anchor='w').grid(row=i, column=4, pady=4, sticky='ew')

    # ---- 结束说明页 (取消 / 超时) ----
    def _build_note(self, text: str) -> None:
        p, b = self.pal, self.body
        self._clear()
        self.win.geometry(f'{WIN_W}x540')
        tk.Label(b, text='麦克风校准', font=font(16, True), bg=p.bg, fg=p.fg).pack(anchor='w', pady=(8, 0))
        tk.Label(b, text=text, font=font(11), bg=p.bg, fg=p.muted, anchor='w', justify='left',
                 wraplength=WIN_W - 60).pack(anchor='w', pady=(10, 0))
        tk.Label(b, text='说话已恢复正常, 增益保持原样', font=font(10), bg=p.bg, fg=p.muted,
                 anchor='w').pack(anchor='w')
        btns = tk.Frame(b, bg=p.bg)
        btns.pack(fill='x', side='bottom', pady=(16, 0))
        Button(btns, p, '再校准一次', self.restart).pack(side='right')
        Button(btns, p, '关闭', self.close, primary=True).pack(side='right', padx=(0, 8))

    # ---- 状态刷新 ----
    def _tick(self) -> None:
        if self._closed:
            return
        from core.client import calibration
        snap = calibration.snapshot()
        if not snap.get('active'):
            note = calibration.last_note()
            if self.phase != 'note':
                self.phase = 'note'
                self._build_note(note or '已结束')
            return
        if snap['done']:
            sig = tuple(sorted((k, str(v)) for k, v in snap['result'].items() if k != 'tips'))
            if sig != self.done_sig:
                self.phase, self.done_sig, self.rows = 'done', sig, snap['rows']
                self._build_done(snap['result'], snap['rows'])
            return
        self.phase, self.rows = 'run', snap['rows']
        self._paint(snap)
        self._after = self.win.after(TICK_MS, self._tick)

    def _paint(self, snap: dict) -> None:
        p = self.pal
        rows = snap['rows']
        sig = (snap['idx'], snap['dev_name'], snap['gain'], snap['note'], len(rows),
               rows[-1].get('hyp') if rows else None)
        if sig != self.sig:
            self.sig = sig
            self.lb_step.configure(text=f"{snap['idx'] + 1} / {snap['total']}")
            self.lb_dev.configure(text=f"正在录 · {snap['dev_name']}")
            self.lb_note.configure(text=snap['note'], fg=p.accent if snap['note'] else p.muted)
            for i, d in enumerate(self.dots):
                d.configure(image=_dot_image(p, i < snap['idx'], i == snap['idx']))
            self.lb_hint.configure(text=f"读这句 · {snap['hint']}")
            self.lb_sent.configure(text=snap['sentence'])
            self.lb_gain.configure(text=f"增益 {snap['gain']:+g} dB")
            if rows:
                last = rows[-1]
                self.lb_last.configure(
                    text=f"第 {snap['idx']}/{snap['total']} 句 · 字错率 {last.get('cer', 0):.0%} · "
                         f"峰值 {_fmt(last.get('peak'))} dBFS · 信噪比 {_fmt(last.get('snr'), ' dB')}\n"
                         f"「{last.get('hyp', '')}」")
            else:
                self.lb_last.configure(text='校准期间说的话不会粘贴到光标处')
        try:
            from core.ui.recording_level import get_level
            level, fresh = get_level()
        except Exception:
            level, fresh = 0.0, False
        recording = bool(getattr(self.app.state, 'recording', False)) and fresh
        norm = min(1.0, max(0.0, (_db(level) + 60) / 60))
        self.lb_meter.configure(image=_meter_image(p, norm if recording else 0.0, recording))
        self.lb_db.configure(text=f'录音中 · {_db(level):.1f} dBFS' if recording else '待录音',
                             fg=p.accent if recording else p.muted)

    # ---- 按钮 ----
    def cancel(self) -> None:
        from core.client import calibration
        calibration.cancel()
        self.close()

    def restart(self) -> None:
        from core.client import calibration
        calibration.cancel('已取消, 重新开始')
        self.close()
        show(self.app)

    def _undo(self) -> None:
        from core.client import calibration
        calibration.restore_gain()
        self.done_sig = None       # 成绩页按新结果重画

    def close(self) -> None:
        global _instance
        if self._closed:
            return
        self._closed = True
        try:
            if self._after:
                self.win.after_cancel(self._after)
        except Exception:
            pass
        if _instance is self:
            _instance = None
        try:
            self.win.destroy()
        except Exception:
            pass


_instance = None


def open_window(master, app, theme_key: str = ''):
    """单实例: 已打开就提到最前 (须在 Tk 线程调用; master = ToastMessageManager 的 root)"""
    global _instance
    if _instance is not None and _instance.win.winfo_exists():
        _instance.win.deiconify()
        _instance.win.lift()
        _instance.win.focus_force()
        return _instance
    from config_client import ClientConfig as Config
    pal = for_theme(theme_key or getattr(Config, 'capsule_theme', 'auto'))
    _instance = CalibWindow(master, app, pal)
    return _instance


def show(app) -> None:
    """托盘 / 设置页等任意线程调用: 开新会话 (已在跑则取消) 并在 Tk 线程开窗"""
    from core.client import calibration
    from core.ui.toast_manager import ToastMessageManager
    mgr = ToastMessageManager()
    if not calibration.start(app):
        mgr.call_soon(_close_open_window)
        return
    mgr.call_soon(lambda: open_window(mgr.root, app))


def _close_open_window() -> None:
    if _instance is not None:
        _instance.close()


if __name__ == '__main__':
    import argparse, os
    ap = argparse.ArgumentParser()
    ap.add_argument('--preview', action='store_true')
    ap.add_argument('--shots', default='')
    ap.add_argument('--theme', default='')
    a = ap.parse_args()

    _FAKE = {'rows': [
        {'hyp': '今天天气不错，我们下午三点在会议室讨论一下新版本的发布计划。', 'cer': 0.11,
         'avg': -34.2, 'peak': -12.1, 'snr': 27.2},
        {'hyp': '把 PostgreSQL 和 Reddis 部署到 Kubernetes 集群里，然后用 Claude Code 改一下配置。',
         'cer': 0.16, 'avg': -35.8, 'peak': -13.4, 'snr': 25.5}],
        'done': False,
        'result': {'device': 'Microphone (ATK N9V2 ULTIMATE dongle)', 'gain_before': 5.0, 'gain_now': 2.0,
                   'applied': True, 'avg_cer': 0.15, 'min_snr': 16.0, 'max_peak': -12.9,
                   'tips': ['最小声那句仍偏低: 麦克风再靠近嘴角一些',
                            '错得多的句子: 接口延迟从 1.5 秒降到 0.8 / 我觉得这个方案应该不行']}}

    class _State:
        recording = False

    class _Stream:
        def current_device_name(self):
            return 'Microphone (ATK N9V2 ULTIMATE dongle)'

    class _App:
        state = _State()
        stream = _Stream()

    def _fake_snapshot():
        from core.client import calibration
        idx = min(len(_FAKE['rows']), len(calibration.SENTENCES) - 1)
        return {'active': True, 'done': _FAKE['done'], 'idx': idx, 'total': len(calibration.SENTENCES),
                'hint': calibration.SENTENCES[idx][0], 'dev_name': 'Microphone (ATK N9V2 ULTIMATE dongle)',
                'gain': 2.0, 'gain0': 5.0, 'sentence': calibration.SENTENCES[idx][1],
                'rows': _FAKE['rows'], 'note': _FAKE.get('note', ''), 'result': _FAKE['result']}

    if a.preview:                    # 预览: 假的校准状态, 不碰麦克风
        from core.client import calibration
        calibration.snapshot = _fake_snapshot
        calibration.last_note = lambda: ''
        calibration.cancel = lambda reason='': {}
        calibration.restore_gain = lambda: False

    root = tk.Tk()
    root.withdraw()
    root.tk.call('tk', 'scaling', 2)
    w = open_window(root, _App(), a.theme)
    if a.shots:
        import ctypes
        import ctypes.wintypes
        from PIL import Image
        os.makedirs(a.shots, exist_ok=True)

        def grab(hwnd):
            u, g = ctypes.windll.user32, ctypes.windll.gdi32
            r = ctypes.wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            width, height = r.right - r.left, r.bottom - r.top
            hdc = u.GetWindowDC(hwnd)
            mdc = g.CreateCompatibleDC(hdc)
            bmp = g.CreateCompatibleBitmap(hdc, width, height)
            g.SelectObject(mdc, bmp)
            u.PrintWindow(hwnd, mdc, 2)                  # PW_RENDERFULLCONTENT
            buf = ctypes.create_string_buffer(width * height * 4)
            bmi = (ctypes.c_uint32 * 10)(40, width, -height, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
            g.GetDIBits(mdc, bmp, 0, height, buf, bmi, 0)
            g.DeleteObject(bmp)
            g.DeleteDC(mdc)
            u.ReleaseDC(hwnd, hdc)
            return Image.frombuffer('RGBA', (width, height), buf, 'raw', 'BGRA', 0, 1).convert('RGB')

        def shoot(i=0):
            from core.ui import recording_level
            if getattr(w.app.state, 'recording', False):
                recording_level.set_level(0.045)      # 电平 0.25 秒不更新就当没在录, 截图前先喂一次再重画
                from core.client import calibration
                w._paint(calibration.snapshot())
            w.win.deiconify()
            w.win.lift()
            w.win.update_idletasks()
            w.win.update()
            hwnd = ctypes.windll.user32.GetParent(w.win.winfo_id()) or w.win.winfo_id()
            grab(hwnd).save(os.path.join(a.shots, f'calib{i}.png'))
            if i == 0:                       # 第二张: 录音中 + 换设备提示 (电平条 / 提示行的样子)
                w.app.state.recording = True
                _FAKE['note'] = '录音设备已换成 Microphone (Jabra UC VOICE 250a), 重新开始校准'
                w.sig = None
                w._tick()
                w.win.after(400, lambda: shoot(1))
            elif i == 1:                     # 第三张: 成绩页
                w.app.state.recording = False
                _FAKE['note'] = ''
                _FAKE['rows'] += [
                    {'hyp': '这份文档需要转译成英文，另外把字符串里的反斜杠转义一下。', 'cer': 0.04,
                     'avg': -36.1, 'peak': -14.0, 'snr': 18.6},
                    {'hyp': '接口延迟从 1.5 秒降到 0.8 秒，吞吐量提升了 40%。', 'cer': 0.44,
                     'avg': -35.5, 'peak': -12.9, 'snr': 21.3},
                    {'hyp': '我觉得这个方案应该不行，明天就可以开始做。', 'cer': 0.16, 'avg': -38.4,
                     'peak': -13.7, 'snr': 32.1},
                    {'hyp': '这件事，我们明天再慢慢讨论吧。', 'cer': 0.0, 'avg': -41.5, 'peak': -14.8, 'snr': 16.1}]
                _FAKE['done'] = True
                w.phase, w.done_sig = '', None
                w._tick()
                w.win.after(400, lambda: shoot(2))
            else:
                root.destroy()
        w.win.after(600, shoot)
    root.mainloop()
