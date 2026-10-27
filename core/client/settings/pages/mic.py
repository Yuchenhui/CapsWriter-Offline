# coding: utf-8
import tkinter as tk

from core.client.settings.widgets import Button, Card, ChoiceGroup, heading, section, font

TITLE = '麦克风'


def build(parent, pal, ctx):
    from core.client.audio import mic_select
    from core.client.audio.default_device_watch import default_capture_id
    from core.client import calibration
    f = tk.Frame(parent, bg=pal.bg)
    heading(f, pal, '麦克风', '录音用 Windows 默认录音设备，在这里切换').pack(anchor='w', pady=(0, 16))
    devs = mic_select.list_capture()
    cur = default_capture_id()
    live = calibration.live_device(ctx.app, devs) if ctx.app is not None else None   # 真正在录的那个 (默认设备可能还没跟上)
    section(f, pal, '录音设备').pack(anchor='w', pady=(0, 8))
    items = []
    for i, name, muted, db in devs:
        tags = []
        if live is not None and i == live[0]:
            tags.append('正在录')
        if i == cur:
            tags.append('Windows 默认')
        detail = f'增益 {db:+.1f} dB' + (' · ' + ' · '.join(tags) if tags else '')
        items.append({'key': i, 'title': name, 'detail': detail, 'warn': '已静音' if muted else ''})
    ChoiceGroup(f, pal, items or [{'key': '', 'title': '没有可用的录音设备', 'disabled': True}], cur,
                lambda k: ctx.do('set_mic', k)).pack(fill='x')
    tk.Label(f, text='设备上的物理静音键 Windows 看不到：录音一直没声音时先检查它', font=font(9), bg=pal.bg,
             fg=pal.muted).pack(anchor='w', pady=(6, 0))
    if live is not None and live[0] != cur:
        tk.Label(f, text=f'正在录的是 {live[1]}，与 Windows 默认设备不同：刚换过设备时默认端点约需 2 秒生效，'
                         f'自动切换开着也会按优先顺序改回来', font=font(9), bg=pal.bg, fg=pal.danger,
                 wraplength=560, justify='left').pack(anchor='w', pady=(4, 0))

    _priority_section(f, pal, ctx, devs)

    gain_dev = (live[0] if live is not None else cur)
    gain_name = (live[1] if live is not None else '')
    section(f, pal, f'当前设备增益 · {gain_name}' if gain_name else '当前设备增益').pack(anchor='w', pady=(22, 8))
    c = Card(f, pal)
    c.pack(fill='x')
    try:
        db, mn, mx, inc = mic_select.gain_info(gain_dev)
        row = tk.Frame(c.body, bg=pal.surface)
        row.pack(fill='x')
        btns = {}

        def paint(sel):                       # 点完按设备实际值重画高亮 (之前不重画, 看着像点不动)
            for v, b in btns.items():
                on = sel is not None and abs(v - sel) < max(inc / 2, 0.05)
                b.configure(font=font(10, on), bg=pal.accent if on else pal.hover,
                            fg=(pal.bg if pal.dark else '#fff') if on else pal.fg)

        def pick(v):
            ctx.do('set_gain', gain_dev, v)
            gi = mic_select.gain_info(gain_dev)
            paint(gi[0] if gi else None)

        for v in mic_select.gain_steps(mn, mx, inc):
            btns[v] = b = tk.Label(row, text=f'{v:+.0f}', padx=10, pady=4, cursor='hand2')
            b.pack(side='left', padx=(0, 6))
            b.bind('<Button-1>', lambda e, v=v: pick(v))
        paint(db)
        tk.Label(c.body, text=f'范围 {mn:+.0f} ~ {mx:+.0f} dB；正常说话峰值不到 0 dBFS 为宜', font=font(9),
                 bg=pal.surface, fg=pal.muted).pack(anchor='w', pady=(8, 0))
    except Exception as e:
        tk.Label(c.body, text=f'读不到增益: {e}', bg=pal.surface, fg=pal.muted).pack(anchor='w')

    section(f, pal, '校准').pack(anchor='w', pady=(22, 8))
    c = Card(f, pal)
    c.pack(fill='x')
    tk.Label(c.body, text='弹出一个校准窗口：依次读 6 句，边读边看电平和每句成绩，跑完给出字错率、信噪比和推荐增益。'
                         '校准按「正在录的设备」绑定，并临时关掉自动切换麦克风，免得中途被换设备。换麦克风或换场所时跑一次。',
             font=font(10), bg=pal.surface, fg=pal.fg, wraplength=560, justify='left').pack(anchor='w')
    Button(c.body, pal, '开始校准', lambda: ctx.do('calibrate'), primary=True).pack(anchor='w', pady=(10, 0))

    section(f, pal, '识别增强').pack(anchor='w', pady=(22, 8))
    c = Card(f, pal)
    c.pack(fill='x')
    from config_client import ClientConfig as Config
    from core.client.settings.widgets import ToggleRow
    ToggleRow(c.body, pal, '小声说话自动增益',
              '气声/小声说话电平低, 云端引擎容易返空或只出半句; 开启后按上一句的说话电平把本句整体放大到目标电平再送识别 (句内增益恒定, 下一句生效)',
              bool(getattr(Config, 'asr_agc', False)), lambda v: ctx.do('set_asr_agc', v)).pack(fill='x', pady=4)
    _remaining = int(ctx.state().get('diagnostic_capture_remaining', 0) or 0)
    ToggleRow(c.body, pal, f'采集对照样本（剩余 {_remaining} 条）' if _remaining else '采集 10 条对照样本',
              '保存每条语音的原始录音、ASR 原文、二次整理结果和最终上屏文本，按 task_id 关联到本机 '
              'voice_samples.jsonl；录音在年/月/assets 下。完成 10 条自动停止，不上传。',
              _remaining > 0, lambda v: ctx.do('set_diagnostic_capture', v)).pack(fill='x', pady=4)

    section(f, pal, '预录').pack(anchor='w', pady=(22, 8))
    c = Card(f, pal)
    c.pack(fill='x')
    ToggleRow(c.body, pal, '预录按键前的声音',
              '麦克风常开时（「说完就关闭麦克风」关着）缓存按下快捷键前最近的声音，补上开口太早被切掉的开头；'
              '只在内存里循环覆盖，不落盘、不上传，按下快捷键后才并入本次录音一起送识别（云端引擎会随录音发送）。'
              '关掉开关立即清空已缓存的声音，改动即时生效',
              bool(getattr(Config, 'pre_roll_enabled', False)), lambda v: ctx.do('set_pre_roll', v)).pack(fill='x', pady=4)
    _pre_roll_sec_row(c.body, pal, ctx)
    return f


def _pre_roll_sec_row(parent, pal, ctx):
    from config_client import ClientConfig as Config
    from core.client.settings import actions
    lo, hi = actions.PRE_ROLL_RANGE
    row = tk.Frame(parent, bg=pal.surface)
    row.pack(fill='x', pady=4)
    tk.Label(row, text='预录时长', font=font(11), bg=pal.surface, fg=pal.fg).pack(side='left')
    tk.Label(row, text='秒', font=font(11), bg=pal.surface, fg=pal.muted).pack(side='right', padx=(6, 0))
    cur = float(getattr(Config, 'pre_roll_seconds', 0.5) or 0.5)
    var = tk.StringVar(value=f'{min(max(cur, lo), hi):g}')
    e = tk.Entry(row, textvariable=var, width=5, justify='center', font=font(11), bg=pal.surface, fg=pal.fg,
                 insertbackground=pal.fg, relief='flat', highlightthickness=1, highlightbackground=pal.border,
                 highlightcolor=pal.accent)
    e.pack(side='right', ipady=3)

    def commit(_e=None):
        """回车 / 离开输入框时保存; 非数字恢复原值, 超范围夹到 0.1~3.0 并回显"""
        from config_client import ClientConfig as Config
        try:
            v = min(max(float(var.get().strip()), lo), hi)
        except ValueError:
            v = float(getattr(Config, 'pre_roll_seconds', 0.5) or 0.5)
        var.set(f'{v:g}')
        ctx.do('set_pre_roll_seconds', v)

    def live(*_):
        """边输边存 (范围内才存): 填完直接点导航切页不会触发回车 / 失焦"""
        try:
            v = float(var.get().strip())
        except ValueError:
            return
        if lo <= v <= hi:
            ctx.do('set_pre_roll_seconds', v)

    var.trace_add('write', live)
    e.bind('<Return>', commit)
    e.bind('<FocusOut>', commit)
    tk.Label(row, text=f'{lo:g}~{hi:g} 秒，缓存按键前最近这么长的声音；只在「说完就关闭麦克风」关着（麦克风常开）时生效',
             font=font(9), bg=pal.surface, fg=pal.muted, wraplength=520, justify='left').pack(anchor='w', pady=(4, 0))


def merge_order(visible: list, saved: list) -> list:
    """拖拽后的新顺序 + 当前没插着的设备 (留在它原来的位置, 免得在家排序把公司的无线麦挤到最后)"""
    out = list(visible)
    for i, p in enumerate(saved):
        if not any(p in n for n in visible):
            out.insert(min(i, len(out)), p)
    return out


def _priority_section(f, pal, ctx, devs):
    from config_client import ClientConfig as Config
    from core.client.settings.widgets import SortList, Toggle
    section(f, pal, '优先顺序').pack(anchor='w', pady=(22, 8))
    row = tk.Frame(f, bg=pal.bg)
    row.pack(fill='x', pady=(0, 8))
    tk.Label(row, text='按顺序自动切换：用第一个有声音的麦克风', font=font(11), bg=pal.bg, fg=pal.fg).pack(side='left')
    Toggle(row, pal, bool(getattr(Config, 'mic_auto', True)), lambda v: ctx.do('set_mic_auto', v), bg=pal.bg).pack(side='right')
    names = [name for _, name, _, _ in devs]           # list_capture 已按当前优先顺序排好

    def save(order):
        saved = list(getattr(Config, 'mic_priority', None) or [])
        ctx.do('set_mic_priority', merge_order(order, saved))

    SortList(f, pal, [{'key': n, 'title': n} for n in names], save).pack(fill='x')
    tk.Label(f, text='拖动排序。无线麦发射器关着、耳机按了静音时录到的是全零，会自动跳到下一个；开着自动切换时手动选的设备会被切回',
             font=font(9), bg=pal.bg, fg=pal.muted, wraplength=560, justify='left').pack(anchor='w', pady=(6, 0))
