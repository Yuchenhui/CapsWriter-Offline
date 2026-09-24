# coding: utf-8
"""
主题胶囊 (本地改 2026-09-24, 按设计包 voice-capsule/README.md 实现; 设计包存档在 pc-tweaks windows/capswriter/voice-capsule/).

五套主题共用一套状态机与过渡 (出现 / 录音->整理 / 整理->完成 / 对勾弹出 / 淡出), 每套主题一个类,
实现 paint_recording / paint_processing / paint_done. 设计包建议 PySide6 自绘, 但打包环境 (internal/) 只有
Pillow + Tk, 故沿用 layered_renderer 的做法: Pillow 3 倍超采样画 RGBA -> 缩小抗锯齿 -> UpdateLayeredWindow 逐像素透明.
动画一律按经过的秒数算 (与帧率无关). 胶囊内不画文字.
"""
from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

SS = 4            # 超采样倍数 (只在胶囊大小的画板上画, 4 倍也便宜; 2 倍时圆头/细边发毛)
MARGIN = 44       # 窗口四周为阴影预留的边距 (阴影下移 12 + 模糊 32)


# ---- 缓动 ----------------------------------------------------------------
def _clamp(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_out(t: float) -> float:
    return 1 - (1 - _clamp(t)) ** 3


def ease_in(t: float) -> float:
    return _clamp(t) ** 3


def ease_in_out(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * _clamp(t))


def wave(t: float, period: float) -> float:
    """0 -> 1 -> 0 的平滑往返 (CSS 0%,100% / 50% 关键帧 + ease-in-out)"""
    return 0.5 - 0.5 * math.cos(2 * math.pi * t / period)


def pop(t: float) -> tuple:
    """对勾弹出 cubic-bezier(.3,1.4,.5,1): 缩放 0.5 -> 1.12 -> 1, 透明度 0 -> 1. 返回 (缩放, 透明度)"""
    t = _clamp(t)
    if t < 0.6:
        k = ease_out(t / 0.6)
        return 0.5 + 0.62 * k, k
    return 1.12 - 0.12 * ease_in_out((t - 0.6) / 0.4), 1.0


def rgba(hexstr: str, a: float = 1.0) -> tuple:
    return int(hexstr[1:3], 16), int(hexstr[3:5], 16), int(hexstr[5:7], 16), round(255 * _clamp(a))


# ---- 画笔: 逻辑像素坐标, 可围绕胶囊中心缩放/下移 ---------------------------
class Pen:
    def __init__(self, w: int, h: int):
        self.img = Image.new('RGBA', (w * SS, h * SS), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img, 'RGBA')   # 'RGBA' 模式: 半透明颜色与底下已画的内容混合
        self.s, self.cx, self.cy, self.dy = 1.0, 0.0, 0.0, 0.0

    def transform(self, s: float, cx: float, cy: float, dy: float = 0.0) -> None:
        self.s, self.cx, self.cy, self.dy = s, cx, cy, dy

    def _x(self, x):
        return (self.cx + (x - self.cx) * self.s) * SS

    def _y(self, y):
        return (self.cy + (y - self.cy) * self.s + self.dy) * SS

    def rrect(self, x0, y0, x1, y1, r, fill=None, outline=None, width=1.0):
        box = (self._x(x0), self._y(y0), self._x(x1), self._y(y1))
        self.d.rounded_rectangle(box, radius=r * self.s * SS, fill=fill, outline=outline,
                                 width=max(1, round(width * self.s * SS)) if outline else 0)

    def circle(self, cx, cy, r, fill=None, outline=None, width=1.0):
        self.rrect(cx - r, cy - r, cx + r, cy + r, r, fill=fill, outline=outline, width=width)

    def polygon(self, pts, fill):
        self.d.polygon([(self._x(x), self._y(y)) for x, y in pts], fill=fill)

    def text(self, x, y, s, font, color):
        """左端、垂直居中对齐; font 须按 SS 倍字号加载"""
        self.d.text((self._x(x), self._y(y)), s, font=font, fill=color, anchor='lm')

    def polyline(self, pts, color, width):
        """圆头圆角折线 (对勾)"""
        p = [(self._x(x), self._y(y)) for x, y in pts]
        w = width * self.s * SS
        self.d.line(p, fill=color, width=max(1, round(w)))
        for x, y in p:
            self.d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=color)


def inset_top(pen: Pen, x0, y0, w, h, alpha):
    """CSS inset 0 1px 0 rgba(255,255,255,alpha): 形状减去下移 1px 的同形状 = 顶部一道随圆角弯曲的细高光"""
    size, r = pen.img.size, h / 2 * SS
    a, b = Image.new('L', size, 0), Image.new('L', size, 0)
    ImageDraw.Draw(a).rounded_rectangle((pen._x(x0), pen._y(y0), pen._x(x0 + w), pen._y(y0 + h)), radius=r, fill=255)
    ImageDraw.Draw(b).rounded_rectangle((pen._x(x0), pen._y(y0 + 1), pen._x(x0 + w), pen._y(y0 + h + 1)), radius=r, fill=255)
    layer = Image.new('RGBA', size, (255, 255, 255, 0))
    layer.putalpha(ImageChops.subtract(a, b).point(lambda v: round(v * alpha)))
    pen.img.alpha_composite(layer)


_FONT_FILES = ('JetBrainsMonoNerdFontMono-Medium.ttf', 'CascadiaMono.ttf', 'consola.ttf')   # 设计: JetBrains Mono 500


def mono_font(px: float):
    """等宽字体 (按 SS 倍字号加载); 都找不到返回 None, 调用方不画计时"""
    import os
    for d in (os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\Windows\Fonts'), os.path.expandvars(r'%WINDIR%\Fonts')):
        for f in _FONT_FILES:
            try:
                return ImageFont.truetype(os.path.join(d, f), round(px * SS))
            except OSError:
                continue
    return None


def mic(pen: Pen, cx, cy, size, color, stroke=2.2):
    """麦克风 (icons/mic.svg: 圆角矩形 x9 y3 6x11 rx3 + 下半圆弧 r7 + 竖线 18->21), 24x24 画布缩放到 size"""
    k = size / 24
    X = lambda v: cx + (v - 12) * k     # noqa: E731
    Y = lambda v: cy + (v - 12) * k     # noqa: E731
    w = stroke * k
    pen.rrect(X(9), Y(3), X(15), Y(14), 3 * k, outline=color, width=w)
    pw = max(1, round(w * pen.s * SS))
    pen.d.arc((pen._x(X(5)), pen._y(Y(4)), pen._x(X(19)), pen._y(Y(18))), 0, 180, fill=color, width=pw)
    for x in (X(5), X(19)):                                   # 弧两端圆头
        pen.circle(x, Y(11), w / 2, fill=color)
    pen.polyline([(X(12), Y(18)), (X(12), Y(21))], color, w)


def sparkle(cx, cy, size, angle_deg=0.0):
    """四角星芒 (icons/sparkle.svg, 24x24 画布) -> 以 (cx, cy) 为中心、边长 size 的多边形顶点"""
    pts = ((12, 2), (14.2, 8.6), (21, 11), (14.2, 13.4), (12, 20), (9.8, 13.4), (3, 11), (9.8, 8.6))
    a = math.radians(angle_deg)
    k = size / 24
    out = []
    for x, y in pts:
        dx, dy = (x - 12) * k, (y - 11) * k
        out.append((cx + dx * math.cos(a) - dy * math.sin(a), cy + dx * math.sin(a) + dy * math.cos(a)))
    return out


# ---- 主题 A: 墨玉 Obsidian -------------------------------------------------
class Obsidian:
    name = '墨玉'
    H = 44
    BAR_N, BAR_W, BAR_GAP = 18, 3, 3
    BARS_W = BAR_N * BAR_W + (BAR_N - 1) * BAR_GAP                  # 105
    W_PROC = 38 + BARS_W + 20                                       # 设计为 14+16+10=40, 与录音态声条起点对齐改 38
    BG, FG, RED, GREEN = '#0a0a0c', '#f4f4f5', '#ff5a4e', '#34c759'
    # CSS: 0 12px 32px rgba(0,0,0,.5); 本地改: 调淡到 .35 后用户嫌不明显 -> .6, 模糊 16 -> 14
    SHADOWS = ((0, 12, 14, (0, 0, 0, 153)),)                        # (dx, dy, 模糊 sigma, RGBA), 可多层 (外发光 = dy 0)

    def shadows(self, mode):
        return self.SHADOWS
    # 各声条的基准高度 / 伸缩周期 / 相位 (取自预览页 height / animation-duration / delay; 每根 scaleY 0.22<->1)
    _BASE = (8, 11, 14, 13, 16, 20, 19, 22, 25, 22, 22, 18, 18, 17, 13, 13, 13, 9)
    _DUR = (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3) * 3
    _DELAY = (0, -.12, -.24, -.35, -.47, -.59, -.71, -.82, -.94, -.06, -.18, -.29, -.41, -.53, -.65, -.76, -.88, 0)

    def __init__(self):
        # 录音计时 (设计: 13px 等宽, 白 55%, 在声条右边隔 14px): 左边距 + 红点 + 间距 + 声波 [+ 14 + 计时] + 右边距
        self.font = mono_font(13)
        self.timer_w = self.font.getlength('0:00') / SS if self.font else 0
        self.W_REC = 16 + 8 + 14 + self.BARS_W + (14 + self.timer_w if self.font else 0) + 18
        self.MAX_W = max(self.W_REC, self.W_PROC)

    def width(self, mode: str) -> float:
        return {'recording': self.W_REC, 'processing': self.W_PROC}.get(mode, self.H)

    def shell(self, pen: Pen, x0, y0, w, mode='recording'):
        h = self.H
        pen.rrect(x0, y0, x0 + w, y0 + h, h / 2, fill=rgba(self.BG), outline=(255, 255, 255, 13), width=1)   # 描边 白 9% -> 5% (用户: 太明显)
        inset_top(pen, x0 + 1, y0 + 1, w - 2, h - 2, 0.04)      # 边框内侧的顶部内高光 6% -> 4%

    def paint_recording(self, pen: Pen, x0, y0, t, level, fade, flatten=0.0):
        cy = y0 + self.H / 2
        # 红点 + 外圈扩散 (0 -> 6px, 55% -> 0, 周期 1.4, 70% 处散尽)
        dx = x0 + 16 + 4
        u = (t % 1.4) / 1.4
        if u < 0.7:
            k = ease_out(u / 0.7)
            pen.circle(dx, cy, 4 + 6 * k, fill=rgba(self.RED, 0.55 * (1 - k) * fade))
        pen.circle(dx, cy, 4, fill=rgba(self.RED, fade))
        # 声波: 每根按设计稿的基准高度与各自节奏伸缩 (scaleY 0.22<->1), 再乘真实音量 —— 不说话落平成 3px;
        # flatten -> 落到 4px (切到整理态)
        bx = x0 + 16 + 8 + 14
        for i in range(self.BAR_N):
            g = wave(t - self._DELAY[i], self._DUR[i])
            h = 3 + (self._BASE[i] * (0.22 + 0.78 * g) - 3) * min(1.0, level * 1.15)
            h = max(3.0, h) + (4 - max(3.0, h)) * flatten
            x = bx + i * (self.BAR_W + self.BAR_GAP)
            pen.rrect(x, cy - h / 2, x + self.BAR_W, cy + h / 2, 1.5, fill=rgba(self.FG, fade))
        if self.font:   # 计时 m:ss (切到整理态时随 fade 淡出)
            pen.text(bx + self.BARS_W + 14, cy, f'{int(t) // 60}:{int(t) % 60:02d}', self.font, rgba('#ffffff', 0.55 * fade))

    def paint_processing(self, pen: Pen, x0, y0, t, fade):
        cy = y0 + self.H / 2
        # 星芒: 设计为 16px 缩放 0.8<->1.1 旋转 0<->45° 来回; 本地改 (用户: 小号十字阶段廉价 / 转得太少):
        # 18px, 每周期 1.6s 同向转 90° (四角星转 90° 与原样重合, 首尾无缝), 先慢后快再慢, 转到一半略放大
        u = (t % 1.6) / 1.6
        f = wave(t, 1.6)
        pen.polygon(sparkle(x0 + 14 + 8, cy, 18 * (0.94 + 0.1 * f), 90 * ease_in_out(u)), fill=rgba('#ffffff', (0.88 + 0.12 * f) * fade))
        # 压平的声条: 4px 高, 依次亮起 (透明度 .18 -> 1, 高度 x1.8), 周期 1.2, 每根延迟 0.05
        bx = x0 + 38
        for i in range(self.BAR_N):
            g = wave(t - 0.05 * i, 1.2)
            h = 4 * (1 + 0.8 * g)
            x = bx + i * (self.BAR_W + self.BAR_GAP)
            pen.rrect(x, cy - h / 2, x + self.BAR_W, cy + h / 2, 1.5, fill=rgba(self.FG, (0.18 + 0.82 * g) * fade))

    def paint_done(self, pen: Pen, cx, cy, k):
        s, a = pop(k)
        r = 10 * s
        pen.circle(cx, cy, r, fill=rgba(self.GREEN, a))
        q = 20 / 18 * s                                               # check 画在 18x18 画布, 显示 20px
        pts = [(cx + (x - 9) * q, cy + (y - 9) * q) for x, y in ((5, 9.2), (7.7, 11.8), (13, 6.6))]
        pen.polyline(pts, rgba(self.BG, a), 2 * q)


# ---- 主题 B: 极光 Aurora ---------------------------------------------------
def _hgrad(size, x_from, x_to, stops):
    """横向渐变 RGBA 图 (size 为 SS 像素). stops = ((位置 0-1, RGBA), ...), 区间外取两端色"""
    w, h = size
    row = Image.new('RGBA', (w, 1))
    px = row.load()
    for x in range(w):
        u = _clamp((x - x_from) / max(1, x_to - x_from))
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if u <= p1:
                k = 0 if p1 == p0 else (u - p0) / (p1 - p0)
                px[x, 0] = tuple(round(a + (b - a) * k) for a, b in zip(c0, c1))
                break
    return row.resize((w, h))


class Aurora:
    name = '极光'
    H = 44
    W_REC = W_PROC = MAX_W = 200
    AREA_W, AREA_H = 160, 28            # 声线区 (左右各 20% 渐隐)
    BG, CYAN, VIOLET = '#0b0d12', '#5ee6c8', '#9b8cff'

    def __init__(self):
        self._cache = {}

    def shadows(self, mode):
        dark = (0, 12, 16, (0, 0, 0, 128))                            # 0 12px 32px rgba(0,0,0,.5)
        if mode == 'recording':
            return ((0, 0, 12, rgba(self.CYAN, 0.10)), dark)          # + 0 0 24px 青 10% 外发光
        if mode == 'done':
            return ((0, 0, 10, rgba(self.CYAN, 0.18)), dark)          # + 0 0 20px 青 18%
        return (dark,)

    def width(self, mode: str) -> float:
        return self.H if mode == 'done' else self.W_REC

    def shell(self, pen: Pen, x0, y0, w, mode='recording'):
        h = self.H
        border = rgba(self.CYAN, 0.35) if mode == 'done' else rgba(self.VIOLET, 0.22)
        pen.rrect(x0, y0, x0 + w, y0 + h, h / 2, fill=rgba(self.BG), outline=border, width=1)

    # 声线区左右 20% 渐隐的蒙版 + 青->紫渐变色层, 画板大小固定, 按画板尺寸缓存
    def _fade_and_grad(self, pen: Pen, ax):
        key = (pen.img.size, round(ax * SS))
        hit = self._cache.get(key)
        if hit is None:
            x0, x1 = pen._x(ax), pen._x(ax + self.AREA_W)
            fade = _hgrad(pen.img.size, x0, x1, ((0, (0, 0, 0, 0)), (0.2, (0, 0, 0, 255)), (0.8, (0, 0, 0, 255)), (1, (0, 0, 0, 0))))
            grad = _hgrad(pen.img.size, x0, x1, ((0, rgba(self.CYAN)), (1, rgba(self.VIOLET))))
            violet = Image.new('RGBA', pen.img.size, rgba(self.VIOLET))
            track = Image.new('L', pen.img.size, 0)
            cy = pen._y(1 + self.H / 2)
            ImageDraw.Draw(track).rounded_rectangle((x0, cy - SS, x1, cy + SS), radius=SS, fill=255)
            band = _hgrad((round(60 * SS), 1), 0, round(60 * SS),
                          ((0, rgba(self.CYAN, 0)), (0.35, rgba(self.CYAN)), (0.7, rgba(self.VIOLET)), (1, rgba(self.VIOLET, 0))))
            hit = self._cache[key] = (fade.getchannel('A'), grad, violet, ImageChops.multiply(track, fade.getchannel('A')), band)
        return hit

    def _stroke(self, pen: Pen, pts, width, color_img, alpha, fade):
        """折线画成蒙版, 乘渐隐与透明度, 用 color_img 上色后叠到画板 (整条线一次成形, 接缝不叠色)"""
        m = Image.new('L', pen.img.size, 0)
        ImageDraw.Draw(m).line([(pen._x(x), pen._y(y)) for x, y in pts], fill=round(255 * _clamp(alpha)),
                               width=max(1, round(width * SS)), joint='curve')
        layer = color_img.copy()
        layer.putalpha(ImageChops.multiply(m, fade))
        pen.img.alpha_composite(layer)

    def paint_recording(self, pen: Pen, x0, y0, t, level, fade, flatten=0.0):
        ax, cy = x0 + (self.MAX_W - self.AREA_W) / 2, y0 + self.H / 2
        vis, grad, violet, _, _ = self._fade_and_grad(pen, ax)
        amp = (0.18 + 0.82 * level) * (1 - flatten)                   # 设计里 0.45-1 呼吸, 这里接真实音量
        # 副线: 振幅 7, 波长 64, 线宽 1.5, 紫 45%, 每 1.5s 左移一个波长
        p2 = [(ax + x, cy + 7 * amp * math.sin(2 * math.pi * (x + 12 + 64 * t / 1.5) / 64)) for x in range(0, self.AREA_W + 1, 2)]
        self._stroke(pen, p2, 1.5, violet, 0.45 * fade, vis)
        # 主线: 振幅 10, 波长 40, 线宽 2.2, 青->紫渐变, 每 0.9s 左移一个波长
        p1 = [(ax + x, cy + 10 * amp * math.sin(2 * math.pi * (x + 40 * t / 0.9) / 40)) for x in range(0, self.AREA_W + 1, 2)]
        self._stroke(pen, p1, 2.2, grad, fade, vis)

    def paint_processing(self, pen: Pen, x0, y0, t, fade):
        ax, cy = x0 + (self.MAX_W - self.AREA_W) / 2, y0 + self.H / 2
        _, _, _, track, band_row = self._fade_and_grad(pen, ax)     # track = 轨道形状 x 两端渐隐
        # 轨道 160x2 白 10%
        layer = Image.new('RGBA', pen.img.size, (255, 255, 255, 0))
        layer.putalpha(track.point(lambda v: v * 26 * fade // 255))
        pen.img.alpha_composite(layer)
        # 光带 60px (透明->青->紫->透明) 从 -60 扫到 160, 周期 1.3, cubic-bezier(.45,0,.55,1) ~ 缓入缓出; 裁在轨道内
        bx = ax - 60 + 220 * ease_in_out((t % 1.3) / 1.3)
        band = Image.new('RGBA', pen.img.size, (0, 0, 0, 0))
        band.paste(band_row.resize((band_row.width, pen.img.height)), (round(pen._x(bx)), 0))
        band.putalpha(ImageChops.multiply(band.getchannel('A'), track.point(lambda v: v * fade)))
        pen.img.alpha_composite(band)

    def paint_done(self, pen: Pen, cx, cy, k):
        s, a = pop(k)
        q = 18 / 18 * s                                              # check 18x18 画布, 显示 18px
        pts = [(cx + (x - 9) * q, cy + (y - 9) * q) for x, y in ((4, 9.4), (7.2, 12.5), (14, 5.8))]
        pen.polyline(pts, rgba(self.CYAN, a), 2.2 * q)


# ---- 主题 C: 微点 Pebble ---------------------------------------------------
class Pebble:
    name = '微点'
    H = 34
    DOT_N, DOT_W, DOT_GAP = 5, 6, 5
    W_REC = W_PROC = MAX_W = 14 + DOT_N * DOT_W + (DOT_N - 1) * DOT_GAP + 14   # 78
    BG, FG, GREEN = '#08080a', '#f4f4f5', '#34c759'
    SHADOWS = ((0, 8, 12, (0, 0, 0, 115)),)                         # 0 8px 24px rgba(0,0,0,.45)
    _DUR = (0.9, 0.7, 0.8, 0.65, 0.95)                              # 预览页各点 animation-duration / delay
    _DELAY = (-0.3, -0.1, -0.55, -0.2, -0.45)

    def shadows(self, mode):
        return self.SHADOWS

    def width(self, mode: str) -> float:
        return self.H if mode == 'done' else self.W_REC

    def shell(self, pen: Pen, x0, y0, w, mode='recording'):
        h = self.H
        pen.rrect(x0, y0, x0 + w, y0 + h, h / 2, fill=rgba(self.BG, 0.92), outline=(255, 255, 255, 20), width=1)

    def _dots(self, x0):
        return [x0 + 14 + i * (self.DOT_W + self.DOT_GAP) for i in range(self.DOT_N)]

    def paint_recording(self, pen: Pen, x0, y0, t, level, fade, flatten=0.0):
        cy = y0 + self.H / 2
        for i, x in enumerate(self._dots(x0)):
            # 高度 6<->20 (各自节奏) x 真实音量; 不说话是 6x6 的圆点
            h = 6 + 14 * wave(t - self._DELAY[i], self._DUR[i]) * min(1.0, level * 1.15)
            h = h + (6 - h) * flatten
            pen.rrect(x, cy - h / 2, x + self.DOT_W, cy + h / 2, 3, fill=rgba(self.FG, fade))

    def paint_processing(self, pen: Pen, x0, y0, t, fade):
        cy = y0 + self.H / 2
        for i, x in enumerate(self._dots(x0)):
            # 依次上跳 5px、透明 .35->1 ("对方正在输入"), 周期 1.1, 每个延迟 0.12; 关键帧 0/60/100% 静止, 30% 最高
            u = ((t - 0.12 * i) % 1.1) / 1.1
            g = ease_in_out(u / 0.3) if u < 0.3 else ease_in_out((0.6 - u) / 0.3) if u < 0.6 else 0.0
            yy = cy - 5 * g
            pen.rrect(x, yy - 3, x + self.DOT_W, yy + 3, 3, fill=rgba(self.FG, (0.35 + 0.65 * g) * fade))

    def paint_done(self, pen: Pen, cx, cy, k):
        s, a = pop(k)
        q = s                                                        # check 16x16 画布, 显示 16px
        pts = [(cx + (x - 8) * q, cy + (y - 8) * q) for x, y in ((3.5, 8.4), (6.4, 11.2), (12.5, 5))]
        pen.polyline(pts, rgba(self.GREEN, a), 2.2 * q)


# ---- 主题 D: 霜白 Frost ----------------------------------------------------
class Frost:
    name = '霜白'
    H = 52
    BAR_N, BAR_W, BAR_GAP = 14, 3, 3
    BARS_W = BAR_N * BAR_W + (BAR_N - 1) * BAR_GAP                  # 81
    W_REC = W_PROC = MAX_W = 10 + 32 + 14 + BARS_W + 20             # 157
    ACCENT, GREEN = '#2f6bff', '#1f9d55'
    SHADOWS = ((0, 10, 14, (30, 34, 52, 36)), (0, 1, 1, (30, 34, 52, 20)))   # 0 10px 28px 14% + 0 1px 2px 8%
    _BASE = (8, 11, 15, 14, 17, 21, 20, 21, 21, 16, 16, 12, 11, 11)
    _DUR = (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3) * 2
    _DELAY = (0, -.12, -.24, -.35, -.47, -.59, -.71, -.82, -.94, -.06, -.18, -.29, -.41, -.53)

    def shadows(self, mode):
        return self.SHADOWS

    def width(self, mode: str) -> float:
        return self.H if mode == 'done' else self.W_REC

    def shell(self, pen: Pen, x0, y0, w, mode='recording'):
        h = self.H
        pen.rrect(x0, y0, x0 + w, y0 + h, h / 2, fill=(255, 255, 255, 219), outline=(0, 0, 0, 15), width=1)

    def paint_recording(self, pen: Pen, x0, y0, t, level, fade, flatten=0.0):
        cy = y0 + self.H / 2
        ccx = x0 + 10 + 16
        # 身后波纹: 同色圆 缩放 1->1.75, 透明 .45->0, 周期 1.4 ease-out
        k = ease_out((t % 1.4) / 1.4)
        pen.circle(ccx, cy, 16 * (1 + 0.75 * k), fill=rgba(self.ACCENT, 0.45 * (1 - k) * fade * (1 - flatten)))
        pen.circle(ccx, cy, 16, fill=rgba(self.ACCENT, fade * (1 - flatten)))
        mic(pen, ccx, cy, 16, rgba('#ffffff', fade * (1 - flatten)))
        bx = x0 + 10 + 32 + 14
        for i in range(self.BAR_N):
            g = wave(t - self._DELAY[i], self._DUR[i])
            h = 3 + (self._BASE[i] * (0.25 + 0.75 * g) - 3) * min(1.0, level * 1.15)
            h = max(3.0, h) + (4 - max(3.0, h)) * flatten
            x = bx + i * (self.BAR_W + self.BAR_GAP)
            pen.rrect(x, cy - h / 2, x + self.BAR_W, cy + h / 2, 1.5, fill=rgba(self.ACCENT, fade))

    def paint_processing(self, pen: Pen, x0, y0, t, fade):
        cy = y0 + self.H / 2
        ccx = x0 + 10 + 16
        pen.circle(ccx, cy, 16, fill=rgba(self.ACCENT, 0.12 * fade))                  # 强调色 12% 浅底
        a0 = 360 * (t % 0.9) / 0.9 - 90                                               # 转圈弧: 1/4 圈, 0.9s 一圈
        pen.d.arc((pen._x(ccx - 13.5 * 16 / 16), pen._y(cy - 13.5), pen._x(ccx + 13.5), pen._y(cy + 13.5)),
                  a0, a0 + 90, fill=rgba(self.ACCENT, fade), width=round(2 * SS))
        pen.polygon(sparkle(ccx, cy, 14), fill=rgba(self.ACCENT, fade))
        bx = x0 + 10 + 32 + 14
        for i in range(self.BAR_N):                                                   # 压平条依次亮起 (同 A, 延迟 0.06)
            g = wave(t - 0.06 * i, 1.2)
            h = 4 * (1 + 0.8 * g)
            x = bx + i * (self.BAR_W + self.BAR_GAP)
            pen.rrect(x, cy - h / 2, x + self.BAR_W, cy + h / 2, 1.5, fill=rgba(self.ACCENT, (0.2 + 0.8 * g) * fade))
        # 底部贴边 2px 进度条: 宽 40%, 从 -100% 滑到 +260% (自身宽), 1.4s, 被胶囊圆角裁掉
        pw = self.W_PROC * 0.4
        px = x0 - pw + (self.W_PROC * 0.4 * 3.6) * ease_in_out((t % 1.4) / 1.4)
        pen.rrect(px, y0 + self.H - 3, px + pw, y0 + self.H - 1, 1, fill=rgba(self.ACCENT, fade))

    def paint_done(self, pen: Pen, cx, cy, k):
        s, a = pop(k)
        pen.circle(cx, cy, 16 * s, fill=rgba(self.GREEN, a))
        q = s                                                                          # check 16px
        pts = [(cx + (x - 8) * q, cy + (y - 8) * q) for x, y in ((3.5, 8.4), (6.4, 11.2), (12.5, 5))]
        pen.polyline(pts, rgba('#ffffff', a), 2.2 * q)


# ---- 主题 E: 光环 Halo -----------------------------------------------------
class Halo:
    name = '光环'
    H = 48
    W_REC = W_PROC = MAX_W = 48                                     # 圆, 不是胶囊
    BG, ACCENT, STAR, GREEN = '#0a0b0e', '#ff7a59', '#f2f2f4', '#34c759'

    _conic = None                     # 类级缓存: 整个进程只建一次 (建一次约十几 ms, 在 Tk 线程里, 别每个胶囊都建)

    def shadows(self, mode):
        dark = (0, 10, 14, (0, 0, 0, 128))                          # 0 10px 28px rgba(0,0,0,.5)
        return ((0, 0, 9, rgba(self.GREEN, 0.2)), dark) if mode == 'done' else (dark,)

    def width(self, mode: str) -> float:
        return self.H

    def shell(self, pen: Pen, x0, y0, w, mode='recording'):
        h = self.H
        border = rgba(self.GREEN, 0.45) if mode == 'done' else (255, 255, 255, 26)
        pen.rrect(x0, y0, x0 + w, y0 + h, h / 2, fill=rgba(self.BG), outline=border, width=1)

    def _ring_img(self):
        """贴边 3px 圆环 (半径 24~27) x 锥形渐变 (前 40% 透明, 之后渐变到强调色), 只建一次, 大小 = 圆环外接方块;
        每帧只旋转这一小块 (原先每帧旋转整窗画板, 一帧 15ms)"""
        if Halo._conic is None:
            n = round(56 * SS)
            c, R = n / 2, n
            img = Image.new('RGBA', (n, n), rgba(self.ACCENT, 0))
            d = ImageDraw.Draw(img)
            for deg in range(0, 360, 3):      # 每 3° 一块: 渐变平滑, 看不出台阶
                u = deg / 360
                d.pieslice((c - R, c - R, c + R, c + R), deg - 90, deg - 87, fill=rgba(self.ACCENT, 0 if u < 0.4 else (u - 0.4) / 0.6))
            ring = Image.new('L', (n, n), 0)
            rd = ImageDraw.Draw(ring)
            rd.ellipse((c - 27 * SS, c - 27 * SS, c + 27 * SS, c + 27 * SS), fill=255)
            rd.ellipse((c - 24 * SS, c - 24 * SS, c + 24 * SS, c + 24 * SS), fill=0)
            img.putalpha(ImageChops.multiply(img.getchannel('A'), ring))
            Halo._conic = img
        return Halo._conic

    def paint_under(self, pen: Pen, cx, cy, t, level, mode, el):
        if mode == 'recording' or (mode == 'processing' and el < 0.22):
            f = 1.0 if mode == 'recording' else 1 - ease_in_out(el / 0.22)
            # 两层光环随音量呼吸: 内层 强调色 33% x (.9<->.4) 缩放 1-1.3; 外层 强调色 x (.25<->.1) 缩放 1.1-1.45
            l1 = min(1.0, level * 1.15) * (0.6 + 0.4 * wave(t, 1.1))
            l2 = min(1.0, level * 1.15) * (0.6 + 0.4 * wave(t + 0.4, 1.6))
            pen.circle(cx, cy, 24 * (1.1 + 0.35 * l2), fill=rgba(self.ACCENT, (0.25 - 0.15 * l2) * f))
            pen.circle(cx, cy, 24 * (1 + 0.3 * l1), fill=rgba(self.ACCENT, 0.33 * (0.9 - 0.5 * l1) * f))
        if mode == 'processing':
            # 贴边 3px 圆环 (24~27px), 锥形渐变 1.1s 一圈
            f = ease_in_out(el / 0.22)
            ring = self._ring_img().rotate(-360 * (t % 1.1) / 1.1, resample=Image.BICUBIC)
            if f < 1:
                ring.putalpha(ring.getchannel('A').point(lambda v: round(v * f)))
            pen.img.alpha_composite(ring, (round(pen._x(cx) - ring.width / 2), round(pen._y(cy) - ring.height / 2)))

    def paint_recording(self, pen: Pen, x0, y0, t, level, fade, flatten=0.0):
        mic(pen, x0 + 24, y0 + 24, 20, rgba(self.ACCENT, fade))

    def paint_processing(self, pen: Pen, x0, y0, t, fade):
        f = wave(t, 1.4)                                             # 星芒 缩放 .85<->1.1, 透明 .75<->1, 1.4s
        pen.polygon(sparkle(x0 + 24, y0 + 24, 18 * (0.85 + 0.25 * f)), fill=rgba(self.STAR, (0.75 + 0.25 * f) * fade))

    def paint_done(self, pen: Pen, cx, cy, k):
        s, a = pop(k)
        q = 20 / 16 * s                                              # check 16x16 画布, 显示 20px
        pts = [(cx + (x - 8) * q, cy + (y - 8) * q) for x, y in ((3.5, 8.4), (6.4, 11.2), (12.5, 5))]
        pen.polyline(pts, rgba(self.GREEN, a), 2.2 * q)


THEMES = {'obsidian': Obsidian, 'aurora': Aurora, 'pebble': Pebble, 'frost': Frost, 'halo': Halo}


def get(name: str):
    cls = THEMES.get(name)
    return cls() if cls else None


# ---- 状态控制器: 过渡时长按设计包「状态机与切换动画」表 --------------------
APPEAR, TO_PROC, SHRINK, POP, HOLD, FADE = 0.18, 0.22, 0.26, 0.45, 0.8, 0.2


class Capsule:
    """每帧给出整窗图像. 调用方只需报告模式 (recording / processing / done) 与 0-1 的音量."""

    def __init__(self, theme):
        self.th = theme
        self.CW, self.CH = int(math.ceil(theme.MAX_W)) + 2, int(theme.H) + 2    # 胶囊画板 (四周留 1px 给抗锯齿)
        self.W, self.H = self.CW - 2 + 2 * MARGIN, self.CH - 2 + 2 * MARGIN      # 整窗 (含阴影边距)
        self.t0 = None
        self.mode = 'recording'
        self.since = 0.0               # 进入当前模式的时刻
        self.level = 0.0               # 平滑后的音量
        self._last = None
        self._shadow = {}
        self._shells = {}              # 宽 -> (外壳 1x RGBA, 蒙版 1x L); 4 倍精度画一次缓存, 平稳状态每帧复用

    def set_mode(self, mode: str, now: float) -> None:
        if mode != self.mode:
            self.mode, self.since = mode, now

    def interval_ms(self, now: float) -> int:
        """帧间隔 16ms (60fps; 需胶囊显示期间 timeBeginPeriod(1), 否则 Tk 定时被拖到 15.6ms 刻度只有 ~38fps).
        只在胶囊大小的画板上画, 每帧约 1ms"""
        return 1000 / 60

    def finished(self, now: float) -> bool:
        return self.mode == 'done' and now - self.since >= SHRINK + POP + HOLD + FADE

    def _shadow_img(self, w: int, mode: str):
        """阴影 + 外发光层 (整窗 1 倍), 按 (胶囊宽度, 模式) 缓存 —— 缩成圆的过渡里宽度在变, 各模式发光不同"""
        img = self._shadow.get((w, mode))
        if img is None:
            img = Image.new('RGBA', (self.W, self.H), (0, 0, 0, 0))
            x0 = (self.W - w) / 2
            for dx, dy, blur, color in self.th.shadows(mode):
                layer = Image.new('RGBA', (self.W, self.H), color[:3] + (0,))
                ImageDraw.Draw(layer).rounded_rectangle((x0 + dx, MARGIN + dy, x0 + dx + w, MARGIN + dy + self.th.H),
                                                        radius=self.th.H / 2, fill=color)
                img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))
            self._shadow[(w, mode)] = img
        return img

    def _shell(self, w, mode):
        key = (round(w * 4), mode)
        hit = self._shells.get(key)
        if hit is None:
            if len(self._shells) > 64:     # 过渡里每帧宽度不同, 防无限增长
                self._shells.clear()
            x0 = (self.CW - w) / 2
            pen, mask = Pen(self.CW, self.CH), Pen(self.CW, self.CH)
            self.th.shell(pen, x0, 1, w, mode)
            mask.rrect(x0, 1, x0 + w, 1 + self.th.H, self.th.H / 2, fill=(255, 255, 255, 255))
            hit = self._shells[key] = (pen.img.convert('RGBa').reduce(SS).convert('RGBA'),
                                       mask.img.getchannel('A').reduce(SS))
        return hit

    def frame(self, now: float, target_level: float):
        """返回 (RGBA 整窗图像, 整窗不透明度 0-1)"""
        if self.t0 is None:
            self.t0 = self.since = now
        dt = 0.016 if self._last is None else max(0.001, now - self._last)
        self._last = now
        # 音量平滑: 上升快、下降慢 (与帧率无关的指数平滑)
        tau = 0.03 if target_level > self.level else 0.15
        self.level += (target_level - self.level) * (1 - math.exp(-dt / tau))

        th, t = self.th, now - self.t0
        el = now - self.since
        x0 = lambda w: (self.CW - w) / 2   # noqa: E731
        y0 = 1

        # 出现: 透明度 0->1, 缩放 0.9->1, 上移 6px; 消失: 透明度 1->0, 缩放 1->0.9
        a = ease_out(t / APPEAR)
        s, dy = 0.9 + 0.1 * a, 6 * (1 - a)
        if self.mode == 'done' and el > SHRINK + POP + HOLD:
            k = ease_in((el - SHRINK - POP - HOLD) / FADE)
            a, s = 1 - k, 1 - 0.1 * k

        inner = Pen(self.CW, self.CH)
        if self.mode == 'recording':
            w = th.width('recording')
            th.paint_recording(inner, x0(w), y0, t, self.level, 1.0)
        elif self.mode == 'processing':
            m = ease_in_out(el / TO_PROC)
            w = th.width('recording') + (th.width('processing') - th.width('recording')) * m
            if m < 1:
                th.paint_recording(inner, x0(w), y0, t, self.level, 1 - m, flatten=m)
            th.paint_processing(inner, x0(w), y0, t, m)
        else:   # done: 先缩成圆 (整理内容淡出), 再弹对勾
            m = ease_in_out(el / SHRINK)
            w = th.width('processing') + (th.H - th.width('processing')) * m
            if m < 1:
                th.paint_processing(inner, x0(w), y0, t, 1 - ease_out(m / 0.6))   # 缩到 60% 前淡完
            if el > SHRINK:
                th.paint_done(inner, self.CW / 2, 1 + th.H / 2, (el - SHRINK) / POP)

        # 内容 (4 倍画, 预乘后缩小) 按外壳蒙版裁剪 -> 叠到缓存的外壳上
        shell, mask = self._shell(w, self.mode)
        content = inner.img.convert('RGBa').reduce(SS).convert('RGBA')
        content.putalpha(ImageChops.multiply(content.getchannel('A'), mask))
        cap = shell.copy()
        cap.alpha_composite(content)
        if abs(s - 1) > 1e-3:          # 出现/消失时整体缩放 (只有前 0.18s 和最后 0.2s)
            cap = cap.resize((max(1, round(self.CW * s)), max(1, round(self.CH * s))), Image.LANCZOS)
        img = self._shadow_img(max(int(th.H), round(w)), self.mode).copy()
        if hasattr(th, 'paint_under'):           # 画在胶囊外面的东西 (E 光环): 整窗画板, 不裁剪
            under = Pen(self.W, self.H)
            th.paint_under(under, self.W / 2, self.H / 2 + dy, t, self.level, self.mode, el)
            img.alpha_composite(under.img.convert('RGBa').reduce(SS).convert('RGBA'))
        img.alpha_composite(cap, (round((self.W - cap.width) / 2), round(MARGIN - 1 + (self.CH - cap.height) / 2 + dy)))
        return img, a
