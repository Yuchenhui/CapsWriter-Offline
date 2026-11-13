# coding: utf-8
import tkinter as tk

from core.client.settings.widgets import Card, ChoiceGroup, Toggle, heading, section, font

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
    _output_mode_card(f, pal, ctx)
    return f


OUTPUT_MODE_ITEMS = (
    ('clipboard', '剪贴板粘贴', '默认。写入剪贴板后模拟 Ctrl-V，行为与以前完全一致'),
    ('tsf', '原生直插（试验）', '经 TSF 把文字直接写进当前窗口，x64、仅单行'),
)


def _output_mode_card(parent, pal, ctx):
    c = Card(parent, pal)
    c.pack(fill='x', pady=(12, 0))
    section(c.body, pal, '文本上屏', bg=pal.surface).pack(anchor='w', pady=(0, 8))
    mode = ctx.state().get('output_mode', 'clipboard')
    if mode not in ('clipboard', 'tsf'):
        mode = 'clipboard'
    ChoiceGroup(c.body, pal, [{'key': k, 'title': t, 'detail': d} for k, t, d in OUTPUT_MODE_ITEMS],
                mode, lambda k: ctx.do('set_output_mode', k)).pack(fill='x')
    tk.Label(c.body, text=_nb('原生直插需先在 Windows 设置 > 时间和语言 > 语言和输入法里添加并选中「CapsWriter Voice」'
              '输入法（这里不做输入法注册，也不改默认输入法），且当前前台应用加载了该输入法才能直插。'
              '找不到服务、焦点变化、超时或含换行/控制字符的那一句不会上屏，也不会改用剪贴板，'
              '完整文本保留在历史记录里。'),
             font=font(9), bg=pal.surface, fg=pal.muted, wraplength=520, justify='left').pack(anchor='w', pady=(8, 0))


def _max_record_row(parent, pal, ctx, value, rng):
    lo, hi = rng
    row = tk.Frame(parent, bg=pal.surface)
    row.pack(fill='x', pady=6)
    head = tk.Frame(row, bg=pal.surface)
    head.pack(fill='x')
    tk.Label(head, text='单次录音上限', font=font(11), bg=pal.surface, fg=pal.fg).pack(side='left')
    tk.Label(head, text='秒', font=font(11), bg=pal.surface, fg=pal.muted).pack(side='right', padx=(6, 0))
    var = tk.StringVar(value=str(min(max(value or hi, lo), hi)))
    e = tk.Entry(head, textvariable=var, width=5, justify='center', font=font(11), bg=pal.surface, fg=pal.fg,
                 insertbackground=pal.fg, relief='flat', highlightthickness=1, highlightbackground=pal.border,
                 highlightcolor=pal.accent)
    e.pack(side='right', ipady=3)

    def commit(_e=None):
        """回车 / 离开输入框时保存; 非数字恢复原值, 超范围夹到 10~180 并回显"""
        from config_client import ClientConfig as Config
        try:
            sec = min(max(int(var.get().strip()), lo), hi)
        except ValueError:
            sec = int(getattr(Config, 'max_record_sec', 30) or hi)
        var.set(str(sec))
        ctx.do('set_max_record', sec)

    def live(*_):
        """边输边存 (范围内才存): 填完直接点导航切页不会触发回车 / 失焦, 09-26 填的 45 因此没存上"""
        v = var.get().strip()
        if v.isdigit() and lo <= int(v) <= hi:
            ctx.do('set_max_record', int(v))

    var.trace_add('write', live)
    e.bind('<Return>', commit)
    e.bind('<FocusOut>', commit)
    tk.Label(row, text=_nb(f'{lo}~{hi} 秒。一句话最长录多久，到点自动结束（仍按着也不再开录，松开后才能录下一句）；最后 10 秒胶囊闪红，越接近越快'),
             font=font(9), bg=pal.surface, fg=pal.muted, wraplength=520, justify='left').pack(anchor='w', pady=(4, 0))
