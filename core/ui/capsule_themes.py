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

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SS = 2            # 超采样倍数
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

    def polyline(self, pts, color, width):
        """圆头圆角折线 (对勾)"""
        p = [(self._x(x), self._y(y)) for x, y in pts]
        w = width * self.s * SS
        self.d.line(p, fill=color, width=max(1, round(w)))
        for x, y in p:
            self.d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=color)


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
    W_REC = 16 + 8 + 14 + BARS_W + 18                               # 左边距 + 红点 + 间距 + 声波 + 右边距
    W_PROC = 38 + BARS_W + 20                                       # 设计为 14+16+10=40, 与录音态声条起点对齐改 38
    MAX_W = max(W_REC, W_PROC)
    BG, FG, RED, GREEN = '#0a0a0c', '#f4f4f5', '#ff5a4e', '#34c759'
    SHADOW = dict(dy=12, blur=16, alpha=0.5)                        # CSS: 0 12px 32px rgba(0,0,0,.5)
    # 各声条的扰动周期/相位 (取自预览页 animation-duration / delay)
    _DUR = (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3) * 3
    _DELAY = (0, -.12, -.24, -.35, -.47, -.59, -.71, -.82, -.94, -.06, -.18, -.29, -.41, -.53, -.65, -.76, -.88, 0)

    def width(self, mode: str) -> float:
        return {'recording': self.W_REC, 'processing': self.W_PROC}.get(mode, self.H)

    def shell(self, pen: Pen, x0, y0, w):
        h = self.H
        pen.rrect(x0, y0, x0 + w, y0 + h, h / 2, fill=rgba(self.BG), outline=(255, 255, 255, 23), width=1)

    def paint_recording(self, pen: Pen, x0, y0, t, level, fade, flatten=0.0):
        cy = y0 + self.H / 2
        # 红点 + 外圈扩散 (0 -> 6px, 55% -> 0, 周期 1.4, 70% 处散尽)
        dx = x0 + 16 + 4
        u = (t % 1.4) / 1.4
        if u < 0.7:
            k = ease_out(u / 0.7)
            pen.circle(dx, cy, 4 + 6 * k, fill=rgba(self.RED, 0.55 * (1 - k) * fade))
        pen.circle(dx, cy, 4, fill=rgba(self.RED, fade))
        # 声波: 高 6-26, 包络中间高两边低, 各自小扰动; flatten -> 落到 4px (切到整理态)
        bx = x0 + 16 + 8 + 14
        n = self.BAR_N
        for i in range(n):
            env = 1 - abs(i - (n - 1) / 2) / ((n - 1) / 2)
            wob = 0.75 + 0.25 * math.sin(2 * math.pi * (t - self._DELAY[i]) / self._DUR[i])
            h = 6 + 20 * level * (0.4 + 0.6 * env) * wob
            h = h + (4 - h) * flatten
            x = bx + i * (self.BAR_W + self.BAR_GAP)
            pen.rrect(x, cy - h / 2, x + self.BAR_W, cy + h / 2, 1.5, fill=rgba(self.FG, fade))

    def paint_processing(self, pen: Pen, x0, y0, t, fade):
        cy = y0 + self.H / 2
        # 星芒: 缩放 0.8<->1.1, 旋转 0<->45°, 透明度 0.7<->1, 周期 1.6
        f = wave(t, 1.6)
        pen.polygon(sparkle(x0 + 14 + 8, cy, 16 * (0.8 + 0.3 * f), 45 * f), fill=rgba('#ffffff', (0.7 + 0.3 * f) * fade))
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


THEMES = {'obsidian': Obsidian}


def get(name: str):
    cls = THEMES.get(name)
    return cls() if cls else None


# ---- 状态控制器: 过渡时长按设计包「状态机与切换动画」表 --------------------
APPEAR, TO_PROC, SHRINK, POP, HOLD, FADE = 0.18, 0.22, 0.26, 0.45, 0.8, 0.2


class Capsule:
    """每帧给出整窗图像. 调用方只需报告模式 (recording / processing / done) 与 0-1 的音量."""

    def __init__(self, theme):
        self.th = theme
        self.W = int(math.ceil(theme.MAX_W)) + 2 * MARGIN
        self.H = int(theme.H) + 2 * MARGIN
        self.t0 = None
        self.mode = 'recording'
        self.since = 0.0               # 进入当前模式的时刻
        self.level = 0.0               # 平滑后的音量
        self._last = None
        self._shadow = {}
        self._shells = {}              # (宽, 缩放, 位移) -> (外壳 SS 图, 蒙版 SS 灰度); 平稳状态每帧复用

    def set_mode(self, mode: str, now: float) -> None:
        if mode != self.mode:
            self.mode, self.since = mode, now

    def interval_ms(self, now: float) -> int:
        """帧间隔: 出现/切换/完成动画期间 ~64fps, 平稳录音与整理 ~32fps (每帧 3-7ms, 省一半 CPU)"""
        busy = self.mode == 'done' or self.t0 is None or now - self.t0 < APPEAR or now - self.since < TO_PROC + 0.05
        return 15 if busy else 31

    def finished(self, now: float) -> bool:
        return self.mode == 'done' and now - self.since >= SHRINK + POP + HOLD + FADE

    def _shadow_img(self, w: int):
        """阴影层 (1 倍分辨率), 按胶囊宽度缓存 —— 缩成圆的过渡里宽度在变"""
        img = self._shadow.get(w)
        if img is None:
            sh = self.th.SHADOW
            img = Image.new('RGBA', (self.W, self.H), (0, 0, 0, 0))
            x0 = (self.W - w) / 2
            ImageDraw.Draw(img).rounded_rectangle((x0, MARGIN + sh['dy'], x0 + w, MARGIN + sh['dy'] + self.th.H),
                                                  radius=self.th.H / 2, fill=(0, 0, 0, round(255 * sh['alpha'])))
            img = img.filter(ImageFilter.GaussianBlur(sh['blur']))
            self._shadow[w] = img
        return img

    def _shell(self, w, s, dy):
        key = (round(w * 4), round(s * 200), round(dy * 8))
        hit = self._shells.get(key)
        if hit is None:
            if len(self._shells) > 64:     # 过渡里每帧宽度不同, 防无限增长
                self._shells.clear()
            cx, cy = self.W / 2, self.H / 2
            pen, mask = Pen(self.W, self.H), Pen(self.W, self.H)
            for p in (pen, mask):
                p.transform(s, cx, cy, dy)
            self.th.shell(pen, cx - w / 2, MARGIN, w)
            mask.rrect(cx - w / 2, MARGIN, cx + w / 2, MARGIN + self.th.H, self.th.H / 2, fill=(255, 255, 255, 255))
            hit = self._shells[key] = (pen.img, mask.img.getchannel('A'))
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
        cx, cy = self.W / 2, self.H / 2
        x0 = lambda w: cx - w / 2   # noqa: E731
        y0 = MARGIN

        # 出现: 透明度 0->1, 缩放 0.9->1, 上移 6px; 消失: 透明度 1->0, 缩放 1->0.9
        a = ease_out(t / APPEAR)
        s, dy = 0.9 + 0.1 * a, 6 * (1 - a)
        if self.mode == 'done' and el > SHRINK + POP + HOLD:
            k = ease_in((el - SHRINK - POP - HOLD) / FADE)
            a, s = 1 - k, 1 - 0.1 * k

        inner = Pen(self.W, self.H)
        inner.transform(s, cx, cy, dy)
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
                th.paint_done(inner, cx, cy, (el - SHRINK) / POP)

        # 内容裁进胶囊: 蒙版 = 外壳形状 (缩成圆的过渡里内容不会露到外面)
        shell, mask = self._shell(w, s, dy)
        inner.img.putalpha(ImageChops.multiply(inner.img.getchannel('A'), mask))
        base = shell.copy()
        base.alpha_composite(inner.img)

        content = base.convert('RGBa').reduce(SS).convert('RGBA')   # 预乘后缩小, 边缘不发黑
        img = Image.alpha_composite(self._shadow_img(max(int(th.H), round(w * s))), content)
        return img, a
