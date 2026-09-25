# coding: utf-8
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk

from core.client.settings.widgets import heading, font

TITLE = '外观'
THEMES = (('obsidian', '墨玉'), ('aurora', '极光'), ('pebble', '微点'), ('frost', '霜白'), ('halo', '光环'),
          ('dark', '经典 · 深色'), ('light', '经典 · 浅色'), ('auto', '经典 · 跟随系统'))
_LIGHT = {'frost', 'light'}
TILE = (300, 120)


def preview(key: str):
    """真实胶囊一帧 (录音态), 放在深 / 浅色桌面底上; 返回 PIL 图"""
    from core.ui import capsule_themes as ct
    from core.ui.layered_renderer import THEMES as CLASSIC, theme
    light = key in _LIGHT or (key == 'auto' and theme()['bottom'][0] > 128)
    tile = Image.new('RGBA', TILE, (233, 236, 242, 255) if light else (38, 41, 49, 255))
    th = ct.get(key)
    if th is not None:
        ct.set_scale(1.0)
        cap = ct.Capsule(th)
        cap.set_mode('recording', 0.0)
        cap.frame(0.0, 0.55)
        img, _ = cap.frame(0.9, 0.55)
    else:                                          # 经典样式: 按其配色画同尺寸药丸 + 红点 + 声条
        pal = CLASSIC.get(key) or theme()
        img = Image.new('RGBA', (230, 90), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((20, 24, 210, 66), radius=21, fill=pal['bottom'], outline=pal['rim'])
        d.ellipse((38, 39, 50, 51), fill=(255, 90, 78, 255))
        for i, h in enumerate((8, 14, 20, 12, 18, 24, 16, 10, 20, 14, 8, 12)):
            x = 64 + i * 11
            d.rounded_rectangle((x, 45 - h / 2, x + 4, 45 + h / 2), radius=2, fill=(250, 250, 252, 230) if pal['bottom'][0] < 128 else (60, 66, 80, 230))
    tile.alpha_composite(img, ((TILE[0] - img.width) // 2, (TILE[1] - img.height) // 2))
    return tile


def build(parent, pal, ctx):
    f = tk.Frame(parent, bg=pal.bg)
    heading(f, pal, '外观', '录音时胶囊的样式；设置窗口配色也跟着变').pack(anchor='w', pady=(0, 16))
    grid = tk.Frame(f, bg=pal.bg)
    grid.pack(fill='x')
    current = ctx.state().get('capsule_theme', 'auto')
    f._imgs, cards = [], {}

    def pick(k):
        for kk, (card, name) in cards.items():
            on = kk == k
            card.configure(highlightbackground=pal.accent if on else pal.border, highlightthickness=2 if on else 1)
            name.configure(fg=pal.accent if on else pal.fg)
        if ctx.do('set_theme', k) and ctx.on_theme_changed:   # 窗口按新主题配色重建 (延后, 不在点击回调里销毁自己)
            grid.after(50, ctx.on_theme_changed)

    for i, (key, label) in enumerate(THEMES):
        card = tk.Frame(grid, bg=pal.surface, cursor='hand2')
        card.grid(row=i // 2, column=i % 2, padx=(0 if i % 2 == 0 else 12, 0), pady=(0, 12), sticky='nsew')
        grid.columnconfigure(i % 2, weight=1, uniform='t')
        try:
            photo = ImageTk.PhotoImage(preview(key))
            f._imgs.append(photo)
            img = tk.Label(card, image=photo, bg=pal.surface, bd=0)
        except Exception as e:                   # 渲染失败不影响整页
            img = tk.Label(card, text=f'预览失败: {e}', bg=pal.surface, fg=pal.muted, height=6)
        img.pack(padx=10, pady=(10, 6))
        name = tk.Label(card, text=label, font=font(11), bg=pal.surface)
        name.pack(pady=(0, 10))
        cards[key] = (card, name)
        for w in (card, img, name):
            w.bind('<Button-1>', lambda e, k=key: pick(k))
    for k, (card, name) in cards.items():       # 初始选中态 (不触发 do)
        on = k == current
        card.configure(highlightbackground=pal.accent if on else pal.border, highlightthickness=2 if on else 1)
        name.configure(fg=pal.accent if on else pal.fg)
    return f
