# coding: utf-8
import tkinter as tk

from core.client.settings.widgets import Card, Toggle, heading, font

TITLE = '系统'


def _nb(text: str) -> str:
    """Tk 只在空格处折行, 中文长句会整段挤到下一行 ("右 Alt" 后提前断): 空格换成不折行空格"""
    return text.replace(' ', ' ')


def _row(parent, pal, title, detail, value, on_change):
    row = tk.Frame(parent, bg=pal.surface)
    row.pack(fill='x', pady=6)
    Toggle(row, pal, value, on_change).pack(side='right', padx=(12, 0))
    tk.Label(row, text=title, font=font(11), bg=pal.surface, fg=pal.fg, anchor='w').pack(anchor='w')
    tk.Label(row, text=_nb(detail), font=font(9), bg=pal.surface, fg=pal.muted, anchor='w', wraplength=520,
             justify='left').pack(anchor='w', pady=(2, 0))


def build(parent, pal, ctx):
    from config_client import ClientConfig as Config
    from core.client.settings import actions
    f = tk.Frame(parent, bg=pal.bg)
    heading(f, pal, '系统', '启动与麦克风占用').pack(anchor='w', pady=(0, 16))
    c = Card(f, pal)
    c.pack(fill='x')
    _row(c.body, pal, '开机自启', '登录 Windows 后在后台启动（启动文件夹里的 CapsWriter 快捷方式）',
         actions.autostart_on(), lambda v: ctx.do('set_autostart', v))
    _row(c.body, pal, '说完就关闭麦克风',
         '按住右 Alt 才打开麦克风，说完就关（占用指示灭），像微信输入法。'
         '代价：每句开头要等麦克风打开，刚用过约 0.05 秒，隔久了约 0.5 秒；胶囊显示暗点「预热」时还收不到声音，看到声波再开口。'
         '关 = 麦克风一直开着，按下即录',
         float(getattr(Config, 'mic_idle_release_sec', 0) or 0) > 0, lambda v: ctx.do('set_mic_idle', v))
    _max_record_row(c.body, pal, ctx, int(getattr(Config, 'max_record_sec', 30) or 0), actions.MAX_RECORD_RANGE)
    return f


def _max_record_row(parent, pal, ctx, value, rng):
    lo, hi = rng
    row = tk.Frame(parent, bg=pal.surface)
    row.pack(fill='x', pady=6)
    head = tk.Frame(row, bg=pal.surface)
    head.pack(fill='x')
    tk.Label(head, text='单次录音上限', font=font(11), bg=pal.surface, fg=pal.fg).pack(side='left')
    val = tk.Label(head, font=font(11, True), bg=pal.surface, fg=pal.accent)
    val.pack(side='right')
    var = tk.IntVar(value=min(max(value or hi, lo), hi))
    show = lambda *_: val.configure(text=f'{var.get()} 秒')
    s = tk.Scale(row, from_=lo, to=hi, resolution=5, orient='horizontal', variable=var, showvalue=0, command=show,
                 bg=pal.accent, troughcolor=pal.hover, activebackground=pal.accent,   # bg 即拖动块颜色 highlightthickness=0, bd=0,
                 sliderrelief='flat', sliderlength=22, width=10, cursor='hand2')
    s.pack(fill='x', pady=(6, 0))
    s.bind('<ButtonRelease-1>', lambda e: ctx.do('set_max_record', var.get()))   # 松手才保存, 拖动中不写文件
    show()
    tk.Label(row, text=_nb('一句话最长录多久，到点自动结束（仍按着也不再开录，松开后才能录下一句）；最后 10 秒胶囊闪红，越接近越快'),
             font=font(9), bg=pal.surface, fg=pal.muted, wraplength=520, justify='left').pack(anchor='w', pady=(4, 0))
