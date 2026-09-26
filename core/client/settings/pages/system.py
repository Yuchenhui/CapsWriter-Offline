# coding: utf-8
import tkinter as tk

from core.client.settings.widgets import Card, Toggle, heading, font

TITLE = '系统'


def _row(parent, pal, title, detail, value, on_change):
    row = tk.Frame(parent, bg=pal.surface)
    row.pack(fill='x', pady=6)
    Toggle(row, pal, value, on_change).pack(side='right', padx=(12, 0))
    tk.Label(row, text=title, font=font(11), bg=pal.surface, fg=pal.fg, anchor='w').pack(anchor='w')
    tk.Label(row, text=detail, font=font(9), bg=pal.surface, fg=pal.muted, anchor='w', wraplength=520,
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
    _row(c.body, pal, '不用时关闭麦克风',
         f'闲置 {actions.IDLE_RELEASE_SEC} 秒后关掉麦克风（占用指示灭），按右 Alt 时再打开。'
         '代价：重新打开约 0.5 秒，胶囊显示暗点「预热」时还收不到声音，看到声波再开口。关 = 麦克风一直开着，按下即录',
         float(getattr(Config, 'mic_idle_release_sec', 0) or 0) > 0, lambda v: ctx.do('set_mic_idle', v))
    return f
