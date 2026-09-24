# coding: utf-8
"""
录音状态指示胶囊窗口

屏幕中下部（贴近任务栏）的深色圆角胶囊，左侧一颗呼吸的 REC 红点 + 文案，
右侧一组有机跳动的声波条。用于录音期间提示「麦克风正在聆听」，无读秒。
松开按键后切换为「正在转文字」处理态（骨架短横 + 青白扫光，无 REC 点），
直到客户端关闭或 15 秒超时自关。
多显示器时出现在「光标所在的那块屏幕」，而非固定主屏。

设计取向：
    - 近黑胶囊 (#161618) + 轻微通透 (-alpha)，全圆角靠 -transparentcolor 抠出
    - 唯一记忆点 = 右侧缓动跳动的声波条（像真实电平表）
    - 动效克制：淡入 + 波形跳动 + 红点呼吸，仅此三样

由 ToastMessageManager 在其 Tk 线程中创建，动画通过 window.after 驱动，
close_toast 时销毁。
"""

from __future__ import annotations

import math
import time
import random
import tkinter as tk
from core.ui.layered_renderer import LayeredRenderer
from typing import Optional, Callable, Union

from .toast_constants import DEFAULT_FONT_FAMILY
from .toast_logger import get_toast_logger

logger = get_toast_logger(__name__)


def _bottom_margin() -> int:
    """胶囊底边距任务栏的像素间距

    优先读取 config_client.ClientConfig.recording_toast_margin，
    取不到（如非客户端环境）时用默认值 _BOTTOM_MARGIN。
    """
    try:
        from config_client import ClientConfig
        m = getattr(ClientConfig, 'recording_toast_margin', None)
        if isinstance(m, (int, float)):
            return int(m)
    except Exception:
        pass
    return _BOTTOM_MARGIN


def _level_target(raw: float, dt: float) -> float:
    """麦克风 RMS -> 0~1 波形幅度: 按高出底噪的 dB 映射 (经典样式与主题胶囊共用).
    底噪往下平滑跟随 (时间常数 0.11s, 不低于 -75 dB 防全零块), 往上每秒只涨 0.5 dB; dt = 距上次调用的秒数"""
    global _floor_db
    db = 20 * math.log10(max(raw, 1e-6))
    if db < _floor_db:
        _floor_db = max(-75.0, _floor_db + (db - _floor_db) * (1 - math.exp(-dt / 0.11)))
    else:
        _floor_db += _FLOOR_RISE_DB_PER_S * dt
    return min(1.0, max(0.0, (db - _floor_db - _DB_START) / _DB_RANGE)) ** _LEVEL_GAMMA


def _capsule_theme_name() -> str:
    try:
        from config_client import ClientConfig
        return getattr(ClientConfig, 'capsule_theme', 'auto')
    except Exception:
        return 'auto'


def _read_mic_level() -> tuple:
    """读取实时麦克风电平 (level, fresh)；不可用时返回 (0.0, False) 以回退合成动画。"""
    try:
        from .recording_level import get_level
        return get_level()
    except Exception:
        return 0.0, False


def _target_alpha() -> float:
    """胶囊整体不透明度

    优先读取 config_client.ClientConfig.recording_toast_opacity，
    取不到时用默认值 _ALPHA；结果夹到 [0.2, 1.0] 以保证可读与可见。
    """
    val = _ALPHA
    try:
        from config_client import ClientConfig
        m = getattr(ClientConfig, 'recording_toast_opacity', None)
        if isinstance(m, (int, float)):
            val = float(m)
    except Exception:
        pass
    return max(0.2, min(1.0, val))


def _cursor_monitor_workarea() -> Optional[tuple]:
    """返回 (鼠标 x, 鼠标 y, 所在显示器工作区 left, top, right, bottom)

    工作区已自动排除任务栏；用于把胶囊放在鼠标指针旁并收进该屏幕内。
    仅 Windows 有效，失败时返回 None 由调用方降级。
    """
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        pt = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return None

        MONITOR_DEFAULTTONEAREST = 2
        hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ('cbSize', wintypes.DWORD),
                ('rcMonitor', wintypes.RECT),
                ('rcWork', wintypes.RECT),
                ('dwFlags', wintypes.DWORD),
            ]

        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return None

        w = mi.rcWork
        return (pt.x, pt.y, w.left, w.top, w.right, w.bottom)
    except Exception:
        return None


# ---- 设计 token ----------------------------------------------------------
_CHROMA = '#010203'        # 透明抠图色（不会与任何绘制色撞色）
_PILL_BG = '#262c38'       # 本地改: 仿玻璃, 石板蓝灰 (原 #14141a 近黑)
_TEXT_FG = '#f5f5f7'
_ALPHA = 0.88              # 整体通透度（越小越透，文字仍需可读）

# 签名元素：密集声条频谱（白→青渐变），中间高两侧低，带说话般起伏 + 横向流动
_WAVE_W = 76               # 声条区域宽度（像素） 2026-09-24: 84 -> 96 -> 76 (用户嫌整体太大, 整体缩到约 3/4)
_WAVE_AMP = 11             # 声条半振幅（像素，满幅约 2×） 11 -> 15 -> 11 (随胶囊高度 50 -> 38)
_WAVE_SPEED = 0.17         # 相位推进速度（每帧）
_BAR_COUNT = 14            # 声条数量 15 -> 16 -> 14
_BAR_W = 3.5               # 单条宽度（像素，圆头） 3 -> 4 -> 3.5
_BAR_MIN_H = 2             # 静止时的最小半高，避免消失
# 本地改: 动态流动配色 —— 循环色带 (首尾相接), 每根声条按位置取色, 整条色带随时间向右流动
_WAVE_PALETTE = ('#35e0d0', '#4aa8ff', '#9b7bff', '#ff7eb6')   # 青 -> 蓝 -> 紫 -> 粉 -> (回到青)
_WAVE_SPAN = 0.6           # 一排声条覆盖色带的比例 (越小相邻声条颜色越接近)
_WAVE_FLOW = 0.006         # 色带每帧流动量 (~25fps 下约 7 秒转一圈)
_DOT_MIN = 2.0            # 转写中: 暗点直径 (像素; 2.5 -> 2.0 随整体缩小)
_DOT_MAX = 5.0            # 转写中: 光点处直径 (6.0 -> 5.0)
_DOT_SWEEP_S = 0.6        # 光点扫一遍的秒数 (按实际时间算, 与帧率无关; 缓入缓出. 2026-09-24: 原 1.6s 嫌慢, 0.37s 嫌太快不优雅)
_PROC_FRAME_MS = 15       # 转写中帧间隔: 对齐 Windows 15.6ms 计时刻度约 64fps (设 20 会被凑成 31ms); 此状态通常只持续 1 秒左右
_DOT_TAIL = 2.2           # 光点光晕宽度 (点数, 越大拖尾越长)

# 真实电平驱动（拿不到实时电平时回退到合成动画）
# 本地改 2026-09-24: 固定噪声门 + 线性增益 -> 按"高出底噪多少 dB"映射. 实测各句说话 -44 ~ -8 dBFS、底噪 -82 ~ -36 dBFS,
# 固定门限 (-44 dBFS) 下小声几乎不动, 嘈杂处又会空跳. 底噪跟踪: 低了立刻跟下来, 高了每帧只涨 0.02 dB (说话不会被当成底噪)
_FLOOR_INIT_DB = -60.0     # 首次录音前的底噪假设; 之后沿用上一次录音学到的值
_FLOOR_RISE_DB_PER_S = 0.5  # 底噪每秒最多上涨 (dB)
_DB_START = 6.0            # 高出底噪这么多 dB 才开始动
_DB_RANGE = 26.0           # 再高出这么多 dB 到满幅
_LEVEL_GAMMA = 0.7         # 感知曲线（<1 把小音量抬起来）
_floor_db = _FLOOR_INIT_DB  # 跨录音保留
_LEVEL_ATTACK = 0.6        # 变响时的跟随速度（大=更跟手）
_LEVEL_DECAY = 0.18        # 变弱时的回落速度（小=更平滑的余韵）
_LEVEL_FLOOR = 0.06        # 静音时的基线高度占比（越小越贴平）

_FONT_SIZE = 14
_PILL_H = 38               # 胶囊高度（= 圆角直径） 50 -> 38
_PAD_X = 16                # 左右内边距 24 -> 16
_DOT_R = 0                 # 本地改: 去掉 REC 红点 (原 4.5)
_GAP_DOT_TEXT = 0          # 本地改: 原 12, 随红点一起去掉
_GAP_TEXT_WAVE = 20

_BOTTOM_MARGIN = 16        # 胶囊底边距任务栏（工作区底部）的像素间距

# 「正在转文字」处理态（松开按键后等待识别结果期间）
_PROC_LABEL = ''   # 本地改: 不要中文文案, 只留扫光短横当 loading
_PROC_TIMEOUT_MS = 15_000  # 处理态超时自关（毫秒，服务端假死/静默丢结果兜底）


_FRAME_MS = 40             # ~25fps
_FADE_STEP = 0.16          # 每帧淡入增量


class ToastWindowRecording:
    """录音指示胶囊窗口

    构造签名与 ToastWindowLabel / ToastWindowText 保持一致，便于
    ToastMessageManager 统一实例化；多余参数忽略。
    """

    def __init__(
        self,
        parent_root: tk.Tk,
        text: str,
        font_size: int = _FONT_SIZE,
        font_family: str = '',
        bg: str = _PILL_BG,
        fg: str = _TEXT_FG,
        duration: int = 0,
        initial_width: Union[float, int] = 0,
        initial_height: int = 0,
        streaming: bool = True,
        stop_callback: Optional[Callable[[], None]] = None,
        markdown: bool = False,
        editable: bool = False,
    ) -> None:
        self.streaming = True          # 常驻，由 close_toast 销毁
        self._text = text or ''   # 本地改: 只留红点+声波
        self._font_family = font_family if font_family else DEFAULT_FONT_FAMILY
        self._font_size = font_size or _FONT_SIZE
        self._frame = 0
        self._alpha = 0.0
        self._target_alpha = _target_alpha()   # 淡入目标不透明度（读配置）
        self._after_id: Optional[str] = None
        self._level = 0.0              # 平滑后的真实电平（0~1）
        # 波形相位起点随机，避免每次录音从同一形状开始
        self._phase0 = random.uniform(0, 6.283)
        # 状态机：listening（聆听）/ processing（转写中）
        # _mode 可由任意线程写入（update_text），_applied_mode 仅 Tk 线程读改
        self._mode = 'listening'
        self._applied_mode = 'listening'
        self._proc_frames = 0                 # 处理态帧计数（仅驱动扫光动画）
        self._proc_timeout_ms = _PROC_TIMEOUT_MS
        from core.ui.layered_renderer import theme
        self._dot_dim = theme()['dot']        # 转写中暗点颜色, 跟随系统深浅色主题
        self._stop_callback = stop_callback   # 超时自毁时通知持有者回收注册状态
        # 本地改 2026-09-24: 主题胶囊 (core/ui/capsule_themes.py, 托盘「胶囊主题」选); None = 经典样式
        from core.ui import capsule_themes
        self._theme = capsule_themes.get(_capsule_theme_name())
        self._themed = None
        self._last_t = None

        self.window = tk.Toplevel(parent_root)
        self.window.overrideredirect(True)
        self.window.attributes('-topmost', True)
        self._ulw = None   # 本地改: 逐像素透明渲染器 (UpdateLayeredWindow), 建窗后初始化; 失败则退回 Tk 画法

        self.window.configure(bg=_CHROMA)
        self.window.pack_propagate(False)

        # 测量文本，计算胶囊尺寸
        from tkinter import font as tkfont
        self._font = tkfont.Font(family=self._font_family, size=self._font_size)
        text_w = self._font.measure(self._text)
        self._w = int(round(_PAD_X + _DOT_R * 2 + _GAP_DOT_TEXT + text_w
                            + (_GAP_TEXT_WAVE if text_w else 0) + _WAVE_W + _PAD_X))
        self._h = int(_PILL_H)
        if self._theme is not None:
            self._w, self._h = int(math.ceil(self._theme.MAX_W)), int(self._theme.H)

        self.canvas = tk.Canvas(
            self.window, width=self._w, height=self._h,
            bg=_CHROMA, highlightthickness=0, bd=0,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # 本地改 2026-09-24: 定位在鼠标指针正下方 (用户要求; Windows Terminal 不报告文字光标位置, 只能跟鼠标),
        # 底下放不下就放到指针上方, 靠屏幕边时收进工作区. 窗口点击穿透, 不挡鼠标
        margin = _bottom_margin()
        info = _cursor_monitor_workarea()
        if info is not None:
            cx, cy, left, top, right, bottom = info
            x = int(min(max(cx - self._w // 2, left), right - self._w))
            y = cy + margin if cy + margin + self._h <= bottom else cy - margin - self._h
            y = int(min(max(y, top), bottom - self._h))
        else:
            # 降级：主屏底部居中
            sw = self.window.winfo_screenwidth()
            sh = self.window.winfo_screenheight()
            x = int((sw - self._w) // 2)
            y = int(sh - self._h - margin - 48)
        self._geom = (x, y)
        self.window.geometry(f'{self._w}x{self._h}+{x}+{y}')

        # 预存布局坐标
        self._dot_cx = _PAD_X + _DOT_R
        self._text_x = self._dot_cx + _DOT_R + _GAP_DOT_TEXT
        self._wave_x0 = self._text_x + text_w + (_GAP_TEXT_WAVE if text_w else 0)
        self._mid_y = self._h / 2

        self.window.deiconify()
        if self._theme is not None:
            self._ulw = LayeredRenderer.create(self.window, self._w, self._h, *self._geom,
                                               margin=capsule_themes.MARGIN, themed=True)
            if self._ulw is not None:
                self._themed = capsule_themes.Capsule(self._theme)
                # 胶囊显示期间把系统计时器精度提到 1ms (浏览器做动画同样如此), 关窗时恢复; 否则 60fps 定时只能到 ~38fps
                try:
                    import ctypes
                    ctypes.windll.winmm.timeBeginPeriod(1)
                    self.window.bind('<Destroy>', lambda e: e.widget is self.window and ctypes.windll.winmm.timeEndPeriod(1), add='+')
                except Exception:
                    pass
        if self._themed is None:
            self._ulw = LayeredRenderer.create(self.window, self._w, self._h, *self._geom)
        if self._ulw is None:   # 退回 Tk 画法: 抠图色透明 + 整窗 alpha
            try:
                self.window.attributes('-transparentcolor', _CHROMA)
                self.window.attributes('-alpha', 0.0)
            except tk.TclError:
                self._alpha = self._target_alpha
            self._draw_static()
        self._tick()

    # -- 绘制 --------------------------------------------------------------
    def _draw_static(self) -> None:
        """绘制不变的部分：圆角胶囊底（含玻璃细边）+ 文案"""
        # 本地改: 不再画亮色描边 (Tk 线条无抗锯齿, 深底上一圈亮边锯齿明显);
        # 胶囊底用 Pillow 4x 超采样画好再缩小, 边缘平滑过渡. Pillow 不可用时退回 Tk 矢量圆角
        img = self._pill_image()
        if img is not None:
            self.canvas.create_image(0, 0, image=img, anchor='nw')
        else:
            self._round_rect(0, 0, self._w, self._h, self._h / 2, fill=_PILL_BG)
        self.canvas.create_text(
            self._text_x, self._mid_y, text=self._text, anchor='w',
            fill=_TEXT_FG, font=self._font,
        )

    def _pill_image(self):
        """抗锯齿胶囊底: 4x 画 (底色=抠图色) 后缩小; 缓存, 引用挂在 self 上防止被回收"""
        if getattr(self, '_pill_img', None) is not None:
            return self._pill_img
        try:
            from PIL import Image, ImageDraw, ImageTk
            ss = 4
            big = Image.new('RGB', (self._w * ss, self._h * ss), _CHROMA)
            ImageDraw.Draw(big).rounded_rectangle((0, 0, self._w * ss - 1, self._h * ss - 1),
                                                  radius=self._h * ss // 2, fill=_PILL_BG)
            self._pill_img = ImageTk.PhotoImage(big.resize((self._w, self._h), Image.LANCZOS), master=self.canvas)
        except Exception:
            self._pill_img = None
        return self._pill_img

    def _round_rect(self, x1, y1, x2, y2, r, fill='', outline='', width=1, **kw) -> None:
        """本地改: 真圆角 (r 取到高度一半即两端正半圆). 原 smooth polygon 的样条圆角弧度不足"""
        c = self.canvas
        r = min(r, (y2 - y1) / 2, (x2 - x1) / 2)
        d = 2 * r
        corners = ((x1, y1, 90), (x2 - d, y1, 0), (x2 - d, y2 - d, 270), (x1, y2 - d, 180))
        for x, y, start in corners:
            c.create_arc(x, y, x + d, y + d, start=start, extent=90, style='pieslice', fill=fill, outline=fill, **kw)
        c.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=fill, **kw)
        c.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=fill, **kw)
        if outline:
            for x, y, start in corners:
                c.create_arc(x, y, x + d, y + d, start=start, extent=90, style='arc', outline=outline, width=width, **kw)
            for seg in ((x1 + r, y1, x2 - r, y1), (x1 + r, y2, x2 - r, y2), (x1, y1 + r, x1, y2 - r), (x2, y1 + r, x2, y2 - r)):
                c.create_line(*seg, fill=outline, width=width, **kw)

    def _tick(self) -> None:
        """每帧：淡入 + 波形跳动. 关闭时窗口还在但画布已销毁的竞态 -> TclError, 直接停帧"""
        try:
            if not self.window.winfo_exists():
                return
            self._tick_frame()
        except tk.TclError:
            return

    def _tick_frame(self) -> None:
        self._frame += 1

        # 状态切换：update_text 可能从任意线程置 _mode，重绘只在本 Tk 线程做
        if self._mode != self._applied_mode:
            self._applied_mode = self._mode
            if self._mode == 'done':
                if self._themed is None:     # 经典样式没有完成动画: 直接关
                    self._on_proc_timeout()
                    return
            else:
                self._enter_processing()
        if self._themed is not None:
            self._themed_frame()
            return
        self._tick_classic()

    def _themed_frame(self) -> None:
        now = time.perf_counter()
        dt = 0.016 if self._last_t is None else now - self._last_t
        self._last_t = now
        self._themed.set_mode({'listening': 'recording'}.get(self._applied_mode, self._applied_mode), now)
        raw, fresh = _read_mic_level()
        img, a = self._themed.frame(now, _level_target(raw, dt) if fresh else 0.0)
        self._ulw.blit(img, a)
        if self._themed.finished(now):
            self._on_proc_timeout()      # 完成动画播完: 自毁 (同超时路径, 会通知持有者回收注册)
            return
        # 按固定节拍排下一帧 (目标时刻累加, 扣掉本帧绘制与调度开销), 否则 16ms 等待实际成 ~20ms
        step = self._themed.interval_ms(now) / 1000
        due = getattr(self, '_due', now) + step
        if due < now - step:             # 落后太多 (卡顿) 就重新对齐, 不追帧
            due = now + step
        self._due = due
        self._after_id = self.window.after(max(1, round((due - time.perf_counter()) * 1000)), self._tick)

    def _enter_processing(self) -> None:
        self._text = _PROC_LABEL
        self._proc_frames = 0
        self._proc_t0 = time.perf_counter()
        # 本地改: 转写中沿用同一排声条 (行波), 布局不变, 无需重排
        if self._ulw is None:
            self.canvas.delete('all')
            self._draw_static()
        # 超时自关兜底(服务端假死/静默丢结果):一次性 after 定时,不受丢帧漂移
        self.window.after(self._proc_timeout_ms, self._on_proc_timeout)

    def _tick_classic(self) -> None:
        processing = (self._applied_mode == 'processing')

        if processing:
            self._proc_frames += 1   # 驱动扫光动画

        # 淡入
        if self._alpha < self._target_alpha:
            self._alpha = min(self._target_alpha, self._alpha + _FADE_STEP)
            if self._ulw is None:
                try:
                    self.window.attributes('-alpha', self._alpha)
                except tk.TclError:
                    pass

        prims = []   # 本帧动态图元: (x1, y1, x2, y2, 线宽, 颜色), 圆头线段

        # 动效区：处理态 = 骨架文字微光（占位短横 + 循环扫光，像文字即将显影）；
        #         聆听态 = 密集声条频谱（白→青渐变，中间高两侧低，横向流动）
        if processing:
            # 本地改: 转写中 = 竖条收成一排小圆点, 一个"光点"从左往右扫过:
            # 被扫到的圆点变大、变亮、带流动色, 其余是暗小点 —— 与聆听态的跳动竖条一眼可分
            step = _WAVE_W / _BAR_COUNT
            span = _BAR_COUNT + 2 * _DOT_TAIL                      # 光点从左侧外进、右侧外出
            u = ((time.perf_counter() - self._proc_t0) / _DOT_SWEEP_S) % 1.0
            head = u * u * (3 - 2 * u) * span - _DOT_TAIL           # smoothstep: 起步收尾慢, 中间快
            for i in range(_BAR_COUNT):
                k = math.exp(-((i - head) / _DOT_TAIL) ** 2)       # 离光点越近越接近 1
                d = _DOT_MIN + (_DOT_MAX - _DOT_MIN) * k            # 圆点直径
                lit = self._palette_at(i / (_BAR_COUNT - 1) * _WAVE_SPAN - self._frame * _WAVE_FLOW * 2)
                x = self._wave_x0 + (i + 0.5) * step
                prims.append((x, self._mid_y, x, self._mid_y, d, self._lerp(self._dot_dim, lit, k)))
        else:
            p = self._phase0 + self._frame * _WAVE_SPEED
            # 整体响度：优先真实麦克风电平（平滑：起快落慢），
            # 拿不到新鲜电平时回退到合成的“说话般”起伏
            raw, fresh = _read_mic_level()
            if fresh:
                target = _level_target(raw, _FRAME_MS / 1000)
                k = _LEVEL_ATTACK if target > self._level else _LEVEL_DECAY
                self._level += (target - self._level) * k
                speech = _LEVEL_FLOOR + (1.0 - _LEVEL_FLOOR) * self._level
                # 纹理深度随响度：安静时几乎静止，说话时才活跃
                depth = 0.15 + 0.85 * self._level
            else:
                # 本地改: 拿不到电平 = 麦克风还没打开 (闲置释放后的冷启动约 0.5s) -> 一排暗点微微呼吸, 表示"预热中";
                # 原实现画合成的说话般起伏, 会让人以为已经在收音而提前开口
                step = _WAVE_W / _BAR_COUNT
                k = 0.5 + 0.5 * math.sin(self._frame * 0.25)
                d = _DOT_MIN + (_DOT_MAX - _DOT_MIN) * 0.35 * k
                for i in range(_BAR_COUNT):
                    x = self._wave_x0 + (i + 0.5) * step
                    prims.append((x, self._mid_y, x, self._mid_y, d, self._dot_dim))
                speech = None
            step = _WAVE_W / _BAR_COUNT
            for i in range(_BAR_COUNT if speech is not None else 0):
                t = (i + 0.5) / _BAR_COUNT
                env = math.sin(math.pi * t)                       # 中间高、两侧低
                wave = 0.5 + 0.5 * math.sin(t * 11 - p * 3.2) * math.sin(t * 4 + p * 1.6)
                detail = (1.0 - depth) + depth * wave             # depth 小→趋于静止
                half = _WAVE_AMP * env * speech * detail
                if half < _BAR_MIN_H:
                    half = _BAR_MIN_H
                x = self._wave_x0 + (i + 0.5) * step
                prims.append((x, self._mid_y - half, x, self._mid_y + half, _BAR_W,
                              self._palette_at(i / (_BAR_COUNT - 1) * _WAVE_SPAN - self._frame * _WAVE_FLOW)))

        if self._ulw is not None:
            self._ulw.render(prims, self._alpha)
        else:
            self.canvas.delete('dyn')
            for x1, y1, x2, y2, w, c in prims:
                self.canvas.create_line(x1, y1, x2, y2, width=w, fill=c, capstyle=tk.ROUND, tags='dyn')

        self._after_id = self.window.after(_PROC_FRAME_MS if processing else _FRAME_MS, self._tick)

    # -- 工具 --------------------------------------------------------------
    def _palette_at(self, u: float) -> str:
        """循环色带取色: u 取小数部分, 在相邻两个色标之间线性插值"""
        n = len(_WAVE_PALETTE)
        f = (u % 1.0) * n
        i = int(f) % n
        return self._lerp(_WAVE_PALETTE[i], _WAVE_PALETTE[(i + 1) % n], f - int(f))

    @staticmethod
    def _lerp(c1: str, c2: str, t: float) -> str:
        """在两个十六进制颜色间线性插值"""
        t = max(0.0, min(1.0, t))
        a = (int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16))
        b = (int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16))
        r = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
        return f'#{r[0]:02x}{r[1]:02x}{r[2]:02x}'

    def _on_proc_timeout(self) -> None:
        """处理态超时自毁（服务端假死/静默丢结果的兜底）

        由进入处理态时的一次性 window.after 触发。窗口若已正常关闭则跳过，
        避免误动持有者后续新建的胶囊。
        """
        try:
            if not self.window.winfo_exists():
                return
        except tk.TclError:
            return
        # 自毁前通知持有者回收注册状态，避免 stale 引用
        if self._stop_callback is not None:
            try:
                self._stop_callback()
            except Exception:
                pass
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    # 状态切换入口：ToastMessageManager.update_toast 会在调用方线程直接转发到这里
    def update_text(self, new_text: str) -> None:
        """任意文本更新即切换到「正在转文字」处理态

        本窗口唯一的更新语义就是状态切换（展示文案由窗口自持的 _PROC_LABEL 决定，
        与传入内容解耦——避免文案微调静默破坏状态机）。
        内容可携带可选的超时毫秒（'processing:<ms>'）：长录音的转录时延与录音时长
        成正比，固定 15s 会在结果到达前误杀胶囊；取 max 保证不低于默认值，
        解析失败则保持默认。
        可能由非 Tk 线程调用，因此只做原子赋值（先超时后模式，_tick 察觉模式
        切换时超时值已就绪），重绘在 Tk 线程 _tick 中完成。
        """
        if new_text == 'done':            # 本地改 2026-09-24: 文字已上屏 -> 完成态 (主题胶囊播对勾, 经典样式直接关)
            self._mode = 'done'
            return
        try:
            self._proc_timeout_ms = max(_PROC_TIMEOUT_MS, int(new_text.split(':', 1)[1]))
        except (IndexError, ValueError):
            pass
        self._mode = 'processing'

    def set_text(self, new_text: str) -> None:     # pragma: no cover - 兼容占位
        pass
