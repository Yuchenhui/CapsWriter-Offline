# coding: utf-8
"""设置窗口配色: 由当前胶囊主题推出 (深色主题 -> 深色窗口, 霜白 / 经典浅色 -> 浅色窗口)"""
from dataclasses import dataclass


def _hex(rgb) -> str:
    return '#%02x%02x%02x' % tuple(int(round(c)) for c in rgb[:3])


def _rgb(h: str) -> tuple:
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a: str, b: str, t: float) -> str:
    """a 与 b 按 t (0=a, 1=b) 混色"""
    x, y = _rgb(a), _rgb(b)
    return _hex([x[i] + (y[i] - x[i]) * t for i in range(3)])


@dataclass(frozen=True)
class Palette:
    dark: bool
    bg: str          # 窗口底色 (内容区)
    side: str        # 左侧导航底色
    surface: str     # 卡片
    hover: str       # 悬停 / 选中底色
    border: str
    fg: str          # 正文
    muted: str       # 次要文字
    accent: str      # 强调色 (选中 / 开关 / 柱状图)
    danger: str


# 主题 -> (底色, 文字色, 强调色); 取自 core/ui/capsule_themes.py 各主题
_THEMES = {
    'obsidian': ('#0a0a0c', '#f4f4f5', '#34c759'),
    'aurora': ('#0b0d12', '#e9ecf5', '#5ee6c8'),
    'pebble': ('#08080a', '#f4f4f5', '#34c759'),
    'halo': ('#0a0b0e', '#f2f2f4', '#ff7a59'),
    'frost': ('#f6f7fa', '#1c2130', '#2f6bff'),
    'dark': ('#1f232d', '#f4f5f8', '#4c8dff'),
    'light': ('#eef1f6', '#1c2130', '#2f6bff'),
}


def for_theme(key: str) -> Palette:
    if key not in _THEMES:                     # auto: 跟随系统深浅色
        from core.ui.layered_renderer import theme
        key = 'light' if theme()['bottom'][0] > 128 else 'dark'
    bg, fg, accent = _THEMES[key]
    dark = _rgb(bg)[0] < 128
    base = mix(bg, fg, 0.035) if dark else bg
    return Palette(
        dark=dark, bg=base, side=bg if dark else mix(bg, '#000000', 0.03),
        surface=mix(bg, fg, 0.07) if dark else '#ffffff', hover=mix(bg, fg, 0.12) if dark else mix(bg, '#000000', 0.06),
        border=mix(bg, fg, 0.14) if dark else mix(bg, '#000000', 0.10),
        fg=fg, muted=mix(fg, bg, 0.45), accent=accent, danger='#ff5a4e')
