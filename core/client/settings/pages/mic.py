# coding: utf-8
import tkinter as tk

from core.client.settings.widgets import Button, Card, ChoiceGroup, heading, section, font

TITLE = '麦克风'


def build(parent, pal, ctx):
    from core.client.audio import mic_select
    from core.client.audio.default_device_watch import default_capture_id
    f = tk.Frame(parent, bg=pal.bg)
    heading(f, pal, '麦克风', '录音用 Windows 默认录音设备，在这里切换').pack(anchor='w', pady=(0, 16))
    devs = mic_select.list_capture()
    cur = default_capture_id()
    section(f, pal, '录音设备').pack(anchor='w', pady=(0, 8))
    items = [{'key': i, 'title': name, 'detail': f'增益 {db:+.1f} dB', 'warn': '已静音' if muted else ''}
             for i, name, muted, db in devs]
    ChoiceGroup(f, pal, items or [{'key': '', 'title': '没有可用的录音设备', 'disabled': True}], cur,
                lambda k: ctx.do('set_mic', k)).pack(fill='x')
    tk.Label(f, text='设备上的物理静音键 Windows 看不到：录音一直没声音时先检查它', font=font(9), bg=pal.bg,
             fg=pal.muted).pack(anchor='w', pady=(6, 0))

    _priority_section(f, pal, ctx, devs)

    section(f, pal, '当前设备增益').pack(anchor='w', pady=(22, 8))
    c = Card(f, pal)
    c.pack(fill='x')
    try:
        db, mn, mx, inc = mic_select.gain_info(cur)
        row = tk.Frame(c.body, bg=pal.surface)
        row.pack(fill='x')
        btns = {}

        def paint(sel):                       # 点完按设备实际值重画高亮 (之前不重画, 看着像点不动)
            for v, b in btns.items():
                on = sel is not None and abs(v - sel) < max(inc / 2, 0.05)
                b.configure(font=font(10, on), bg=pal.accent if on else pal.hover,
                            fg=(pal.bg if pal.dark else '#fff') if on else pal.fg)

        def pick(v):
            ctx.do('set_gain', cur, v)
            gi = mic_select.gain_info(cur)
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
    tk.Label(c.body, text='依次读 6 句，按最响那句自动设增益，并给出字错率和信噪比。换麦克风或换场所时跑一次。',
             font=font(10), bg=pal.surface, fg=pal.fg, wraplength=560, justify='left').pack(anchor='w')
    Button(c.body, pal, '开始校准', lambda: ctx.do('calibrate'), primary=True).pack(anchor='w', pady=(10, 0))
    return f


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
