# coding: utf-8
"""
实时识别气泡 (本地改 2026-09-25)

按住快捷键时, 在线识别的中间结果显示在胶囊下方 (胶囊在指针上方时显示在胶囊上方) 的气泡里.
- 样式跟随胶囊主题 (capsule_themes 的 BG / 文字色 / 描边 / SHADOWS; 经典样式取 layered_renderer 深浅配色)
- 宽度随文字伸缩, 超过 MAX_W 换行 (中文按字, 英文尽量按词); 最多 MAX_LINES 行, 再多像字幕一样只留最新的
- 打字机效果: 新到的字在 ~0.6s 内匀速打出 (至少每秒 12 字), 识别改写了前文就从改动处重打
渲染与上屏分开: render() 是纯函数 (可离线出图检查), LiveBubble 负责 Tk 线程里的逐帧驱动和分层窗口.
"""
from __future__ import annotations

import ctypes
import math
import os
import time
from ctypes import wintypes
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SS = 3                 # 超采样
FONT_PX = 15           # 以下均为逻辑像素, 乘 scale 得屏幕像素
LINE_H = 23
PAD_X, PAD_Y = 16, 10
MAX_W = 460            # 文字区最大宽度
MAX_LINES = 5
RADIUS_MULTI = 16
GAP = 8                # 与胶囊的间距
MARGIN = 48            # 四周给阴影留的边距 (墨玉阴影下移 12 + 模糊 14; 28 时底部被切出一道硬边)


@dataclass(frozen=True)
class Style:
    bg: tuple                       # RGBA
    fg: tuple                       # RGBA
    border: tuple                   # RGBA
    shadows: tuple                  # ((dx, dy, blur, RGBA), ...)


def _rgba(h: str, a: float = 1.0) -> tuple:
    h = h.lstrip('#')
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (round(a * 255),)


def style_for(theme_key: str) -> Style:
    """theme_key: capsule_theme 配置值 (obsidian / aurora / pebble / frost / halo / auto / light / dark)"""
    from core.ui import capsule_themes as ct
    dark_shadow = ((0, 10, 14, (0, 0, 0, 140)),)
    if theme_key == 'obsidian':
        t = ct.Obsidian
        return Style(_rgba(t.BG), _rgba(t.FG), (255, 255, 255, 13), t.SHADOWS)
    if theme_key == 'aurora':
        t = ct.Aurora
        return Style(_rgba(t.BG), _rgba('#e9ecf5'), _rgba(t.VIOLET, 0.14), dark_shadow)
    if theme_key == 'pebble':
        t = ct.Pebble
        return Style(_rgba(t.BG, 0.92), _rgba(t.FG), (255, 255, 255, 10), t.SHADOWS)
    if theme_key == 'frost':
        t = ct.Frost
        return Style((255, 255, 255, 219), _rgba('#1c2130'), (0, 0, 0, 15), t.SHADOWS)
    if theme_key == 'halo':
        t = ct.Halo
        return Style(_rgba(t.BG), _rgba(t.STAR), (255, 255, 255, 10), dark_shadow)
    from core.ui.layered_renderer import theme, THEMES    # 经典样式: 手选深浅用那一套, auto 跟随系统
    th = THEMES.get(theme_key) or theme()
    light = th['bottom'][0] > 128
    return Style(th['bottom'], _rgba('#1c2130' if light else '#f4f5f8'), th['rim'], ((0, 6, 10, th['shadow']),))


_fonts: dict = {}


def _font(px_: int):
    f = _fonts.get(px_)
    if f is None:
        path = os.path.expandvars(r'%WINDIR%\Fonts\msyh.ttc')
        try:
            f = ImageFont.truetype(path, px_, index=1)       # Microsoft YaHei UI
        except OSError:
            f = ImageFont.load_default(px_)
        _fonts[px_] = f
    return f


def wrap(text: str, font, max_w: float) -> list:
    """按像素宽度折行: 中文按字断, 英文单词尽量不拆 (单词本身超宽才拆)"""
    lines = []
    for para in text.split('\n'):
        line = ''
        for ch in para:
            if font.getlength(line + ch) <= max_w:
                line += ch
                continue
            cut = line.rfind(' ')
            if ch != ' ' and 0 < cut and line[cut + 1:].isascii() and line[cut + 1:].strip():   # 在英文单词中间: 回退到空格处
                lines.append(line[:cut]); line = line[cut + 1:] + ch
            else:
                lines.append(line); line = ch.lstrip()
        lines.append(line)
    return lines


def render(text: str, st: Style, scale: float = 1.0):
    """-> (RGBA 整窗图像, 气泡宽, 气泡高), 尺寸均为屏幕像素; 图像四周含 MARGIN*scale 阴影边距"""
    k = scale
    font = _font(max(8, round(FONT_PX * k * SS)))
    lines = wrap(text, font, MAX_W * k * SS)[-MAX_LINES:]
    text_w = max((font.getlength(l) for l in lines), default=0) / SS
    bw = math.ceil(text_w + 2 * PAD_X * k)
    bh = math.ceil(len(lines) * LINE_H * k + 2 * PAD_Y * k)
    bw = max(bw, bh)                                          # 至少是个圆
    r = bh / 2 if len(lines) == 1 else RADIUS_MULTI * k
    m = round(MARGIN * k)
    W, H = bw + 2 * m, bh + 2 * m

    img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    for dx, dy, blur, color in st.shadows:                   # 阴影 (1 倍画, 高斯模糊)
        layer = Image.new('RGBA', (W, H), color[:3] + (0,))
        ImageDraw.Draw(layer).rounded_rectangle((m + dx * k, m + dy * k, m + dx * k + bw, m + dy * k + bh), radius=r, fill=color)
        img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur * k)))

    big = Image.new('RGBA', (bw * SS, bh * SS), (0, 0, 0, 0))   # 外壳 + 文字 (超采样后缩小)
    d = ImageDraw.Draw(big)
    d.rounded_rectangle((0, 0, bw * SS - 1, bh * SS - 1), radius=r * SS, fill=st.bg, outline=st.border, width=max(1, round(k * SS)))
    asc, desc = font.getmetrics()
    for i, l in enumerate(lines):
        y = (PAD_Y * k + i * LINE_H * k) * SS + (LINE_H * k * SS - (asc + desc)) / 2
        d.text((PAD_X * k * SS, y), l, font=font, fill=st.fg)
    shell = big.convert('RGBa').reduce(SS).convert('RGBA')
    img.alpha_composite(shell, (m, m))
    return img, bw, bh


class Typewriter:
    """显示文字逐步追上目标文字.
    识别改写已显示的字时原地替换 (不删了重打, 否则看着像整句退回去, 2026-09-25 用户反馈); 只有新增的字用打字效果"""
    MIN_CPS, SPREAD = 20.0, 0.25          # 新到的一批字 0.25s 内打完 (0.6 时气泡明显落后于说话)

    def __init__(self):
        self.shown, self._acc = '', 0.0

    def step(self, target: str, dt: float) -> bool:
        """推进 dt 秒; 返回显示内容是否变化"""
        changed = False
        if not target.startswith(self.shown):                  # 识别改写了前文: 已显示的长度内直接换成新字
            self.shown, changed = target[:len(self.shown)], True
        backlog = len(target) - len(self.shown)
        if backlog <= 0:
            self._acc = 0.0
            return changed
        self._acc += max(self.MIN_CPS, backlog / self.SPREAD) * dt
        n = min(backlog, int(self._acc))
        if n <= 0:
            return changed
        self._acc -= n
        self.shown = target[:len(self.shown) + n]
        return True


# ---- 分层窗口 (尺寸可变) ----
from core.ui.layered_renderer import _u32, _g32, _BITMAPINFOHEADER, _BLEND, _GWL_EXSTYLE, _WS_EX   # noqa: E402


class _Surface:
    def __init__(self, window):
        window.update_idletasks()
        self.hwnd = _u32.GetParent(window.winfo_id()) or window.winfo_id()
        _u32.SetWindowLongW(self.hwnd, _GWL_EXSTYLE, _u32.GetWindowLongW(self.hwnd, _GWL_EXSTYLE) | _WS_EX)
        self.hdc_screen = _u32.GetDC(None)
        self.hdc_mem = _g32.CreateCompatibleDC(self.hdc_screen)
        self.size, self.hbmp, self.bits = (0, 0), None, ctypes.c_void_p()

    def present(self, img, x: int, y: int, alpha: float):
        if img.size != self.size:                               # 尺寸变了才重建位图
            if self.hbmp:
                _g32.DeleteObject(self.hbmp)
            bmi = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), img.width, -img.height, 1, 32, 0, 0, 0, 0, 0, 0)
            self.hbmp = _g32.CreateDIBSection(self.hdc_screen, ctypes.byref(bmi), 0, ctypes.byref(self.bits), None, 0)
            _g32.SelectObject(self.hdc_mem, self.hbmp)
            self.size = img.size
        data = img.convert('RGBa').tobytes('raw', 'BGRa')
        ctypes.memmove(self.bits, data, len(data))
        blend = _BLEND(0, 0, max(0, min(255, round(alpha * 255))), 1)
        _u32.UpdateLayeredWindow(self.hwnd, self.hdc_screen, ctypes.byref(wintypes.POINT(x, y)),
                                 ctypes.byref(wintypes.SIZE(*img.size)), self.hdc_mem,
                                 ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), 2)

    def free(self):
        if self.hbmp:
            _g32.DeleteObject(self.hbmp); self.hbmp = None
        if self.hdc_mem:
            _g32.DeleteDC(self.hdc_mem); self.hdc_mem = None
        if self.hdc_screen:
            _u32.ReleaseDC(None, self.hdc_screen); self.hdc_screen = None


class LiveBubble:
    """挂在录音胶囊上: 胶囊窗口每帧调用 tick(target, dt); 只在 Tk 线程使用"""
    FADE = 0.15

    def __init__(self, parent, anchor: tuple, above: bool, workarea, theme_key: str, scale: float):
        import tkinter as tk
        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.geometry('1x1+-32000+-32000')
        self.surface = _Surface(self.win)
        self.anchor, self.above, self.workarea = anchor, above, workarea   # anchor: 胶囊 (x, y, w, h) 屏幕像素
        self.style, self.scale = style_for(theme_key), scale
        self.tw, self.alpha, self.fading = Typewriter(), 0.0, False
        self._img = None

    def tick(self, target: str, dt: float) -> None:
        changed = self.tw.step(target, dt)
        if self.fading:
            self.alpha = max(0.0, self.alpha - dt / self.FADE)
        elif self.tw.shown:
            self.alpha = min(1.0, self.alpha + dt / self.FADE)
        if not self.tw.shown and self._img is None:
            return
        if changed or self._img is None:
            self._img = render(self.tw.shown, self.style, self.scale)
        img, bw, bh = self._img
        m = round(MARGIN * self.scale)
        ax, ay, aw, ah = self.anchor
        gap = round(GAP * self.scale)
        x = ax + aw // 2 - bw // 2
        y = ay - gap - bh if self.above else ay + ah + gap
        if self.workarea:
            left, top, right, bottom = self.workarea
            x = min(max(x, left), right - bw)
            y = min(max(y, top), bottom - bh)
        self.surface.present(img, x - m, y - m, self.alpha)

    def fade_out(self) -> None:
        self.fading = True

    def destroy(self) -> None:
        try:
            self.surface.free()
            self.win.destroy()
        except Exception:
            pass


if __name__ == '__main__':   # 离线出图: python -m core.ui.live_bubble <输出目录>
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else '.'
    samples = {'short': '帮我看一下这个报错',
               'long': '帮我把 PostgreSQL 和 Redis 部署到 Kubernetes 上，然后用 MiniMax 的 M2.7 High Speed 跑一下 DeepSeek 的对比测试，'
                       '看看 Claude Code 里面的效果怎么样。我们周四上线，上线前先把 @README.md 更新一下。服务器地址是 192.168.1.1，端口 8080。'}
    lines = wrap('hello world internationalization 测试', _font(15 * SS), 120 * SS)
    assert all(not l.startswith(' ') for l in lines), lines
    tw = Typewriter()
    assert tw.step('你好世界', 0.1) and tw.shown and len(tw.shown) < 4
    tw.shown = '帮我配置一下'; assert tw.step('帮我post一下这个', 0.016)
    assert tw.shown.startswith('帮我post') and len(tw.shown) >= 6, tw.shown   # 改写原地替换, 不退回重打
    for key in ('obsidian', 'aurora', 'pebble', 'frost', 'halo', 'dark', 'light'):
        for name, s in samples.items():
            bg = Image.new('RGBA', (560, 260), (236, 239, 244, 255) if key in ('frost', 'light') else (40, 44, 52, 255))
            img, bw, bh = render(s, style_for(key), 1.0)
            bg.alpha_composite(img, (10, 10))
            bg.save(os.path.join(out, f'bubble_{key}_{name}.png'))
    print('live_bubble selftest ok')


# ---- 提示气泡: 云端识别失败等提示, 样式同实时识别气泡, 字用警示色; 胶囊关了也停留几秒再淡出 ----
_last = None       # 最近一次胶囊的 (anchor, above, workarea, theme_key, scale), 胶囊显示时记下


def remember(anchor: tuple, above: bool, workarea, theme_key: str, scale: float) -> None:
    global _last
    _last = (anchor, above, workarea, theme_key, scale)


def notice(text: str, seconds: float = 4.0) -> bool:
    """任意线程: 在最近一次胶囊的位置显示提示; 还没显示过胶囊时返回 False"""
    if _last is None:
        return False
    from core.ui.toast_manager import ToastMessageManager
    m = ToastMessageManager()
    m.call_soon(lambda: _Notice(m.root, text, seconds))
    return True


class _Notice:
    def __init__(self, root, text: str, seconds: float):
        from dataclasses import replace
        anchor, above, workarea, theme_key, scale = _last
        self.b = LiveBubble(root, anchor, above, workarea, theme_key, scale)
        light = sum(self.b.style.bg[:3]) > 384
        self.b.style = replace(self.b.style, fg=(180, 83, 9, 255) if light else (255, 184, 64, 255))   # 琥珀色
        self.b.tw.shown = self.text = text                   # 整句直接显示, 不走打字效果
        self.root, self.left, self.t = root, seconds, time.perf_counter()
        self._tick()

    def _tick(self):
        now = time.perf_counter()
        dt, self.t = now - self.t, now
        self.left -= dt
        if self.left <= 0:
            self.b.fade_out()
        self.b.tick(self.text, dt)
        if self.b.fading and self.b.alpha <= 0:
            self.b.destroy()
            return
        self.root.after(16, self._tick)
