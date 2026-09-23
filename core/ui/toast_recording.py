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


def _level_gain() -> float:
    """波形灵敏度（RMS→满幅增益），读 config，夹到 [1, 80]。"""
    val = _LEVEL_GAIN
    try:
        from config_client import ClientConfig
        m = getattr(ClientConfig, 'recording_toast_sensitivity', None)
        if isinstance(m, (int, float)):
            val = float(m)
    except Exception:
        pass
    return max(1.0, min(80.0, val))


def _level_gate() -> float:
    """噪声门阈值（RMS），读 config，夹到 [0, 0.2]。"""
    val = _LEVEL_GATE
    try:
        from config_client import ClientConfig
        m = getattr(ClientConfig, 'recording_toast_noise_gate', None)
        if isinstance(m, (int, float)):
            val = float(m)
    except Exception:
        pass
    return max(0.0, min(0.2, val))


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
    """返回光标所在显示器的工作区 (left, top, right, bottom)

    工作区已自动排除任务栏；用于把胶囊放在焦点屏幕的底部而非固定主屏。
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
        return (w.left, w.top, w.right, w.bottom)
    except Exception:
        return None


# ---- 设计 token ----------------------------------------------------------
_CHROMA = '#010203'        # 透明抠图色（不会与任何绘制色撞色）
_PILL_BG = '#262c38'       # 本地改: 仿玻璃, 石板蓝灰 (原 #14141a 近黑)
_TEXT_FG = '#f5f5f7'
_ALPHA = 0.88              # 整体通透度（越小越透，文字仍需可读）

# 签名元素：密集声条频谱（白→青渐变），中间高两侧低，带说话般起伏 + 横向流动
_WAVE_W = 84               # 声条区域宽度（像素）
_WAVE_AMP = 11             # 声条半振幅（像素，满幅约 2×）
_WAVE_SPEED = 0.17         # 相位推进速度（每帧）
_BAR_COUNT = 15            # 声条数量
_BAR_W = 3                 # 单条宽度（像素，圆头）
_BAR_MIN_H = 2             # 静止时的最小半高，避免消失
# 本地改: 动态流动配色 —— 循环色带 (首尾相接), 每根声条按位置取色, 整条色带随时间向右流动
_WAVE_PALETTE = ('#35e0d0', '#4aa8ff', '#9b7bff', '#ff7eb6')   # 青 -> 蓝 -> 紫 -> 粉 -> (回到青)
_WAVE_SPAN = 0.6           # 一排声条覆盖色带的比例 (越小相邻声条颜色越接近)
_WAVE_FLOW = 0.006         # 色带每帧流动量 (~25fps 下约 7 秒转一圈)
_DOT_MIN = 2.5            # 转写中: 暗点直径 (像素)
_DOT_MAX = 6.0            # 转写中: 光点处直径
_DOT_DIM = '#4a5163'      # 转写中: 暗点颜色
_DOT_SPEED = 0.45         # 光点移动速度 (点/帧, ~25fps 下约 1.6 秒扫一遍)
_DOT_TAIL = 2.2           # 光点光晕宽度 (点数, 越大拖尾越长)

# 真实电平驱动（拿不到实时电平时回退到合成动画）
_LEVEL_GATE = 0.010        # 噪声门：RMS 低于此值视为静音（减掉底噪，避免没说话也在动）
_LEVEL_GAIN = 12.0         # 过门后 RMS→满幅的增益（越大越灵敏，按麦克风口味调）
_LEVEL_GAMMA = 0.6         # 感知曲线（<1 把小音量抬起来，正常说话就能填到大半）
_LEVEL_ATTACK = 0.6        # 变响时的跟随速度（大=更跟手）
_LEVEL_DECAY = 0.18        # 变弱时的回落速度（小=更平滑的余韵）
_LEVEL_FLOOR = 0.06        # 静音时的基线高度占比（越小越贴平）

_FONT_SIZE = 14
_PILL_H = 50               # 胶囊高度（= 圆角直径）
_PAD_X = 24                # 左右内边距（留出更宽松的边界）
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
        self._gain = _level_gain()     # 灵敏度（读配置，创建时定）
        self._peak = 0.0               # 本地改: 自动增益用的近期峰值 (缓慢衰减)
        self._gate = _level_gate()     # 噪声门（读配置，创建时定）
        # 波形相位起点随机，避免每次录音从同一形状开始
        self._phase0 = random.uniform(0, 6.283)
        # 状态机：listening（聆听）/ processing（转写中）
        # _mode 可由任意线程写入（update_text），_applied_mode 仅 Tk 线程读改
        self._mode = 'listening'
        self._applied_mode = 'listening'
        self._proc_frames = 0                 # 处理态帧计数（仅驱动扫光动画）
        self._proc_timeout_ms = _PROC_TIMEOUT_MS
        self._stop_callback = stop_callback   # 超时自毁时通知持有者回收注册状态

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

        self.canvas = tk.Canvas(
            self.window, width=self._w, height=self._h,
            bg=_CHROMA, highlightthickness=0, bd=0,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # 定位：光标所在屏幕的底部居中，贴近任务栏
        margin = _bottom_margin()
        area = _cursor_monitor_workarea()
        if area is not None:
            left, top, right, bottom = area
            x = int(left + (right - left - self._w) // 2)
            y = int(bottom - self._h - margin)
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
            self._text = _PROC_LABEL
            self._proc_frames = 0
            # 本地改: 转写中沿用同一排声条 (行波), 布局不变, 无需重排
            if self._ulw is None:
                self.canvas.delete('all')
                self._draw_static()
            # 超时自关兜底(服务端假死/静默丢结果):一次性 after 定时,不受丢帧漂移
            self.window.after(self._proc_timeout_ms, self._on_proc_timeout)

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
            head = (self._proc_frames * _DOT_SPEED) % span - _DOT_TAIL
            for i in range(_BAR_COUNT):
                k = math.exp(-((i - head) / _DOT_TAIL) ** 2)       # 离光点越近越接近 1
                d = _DOT_MIN + (_DOT_MAX - _DOT_MIN) * k            # 圆点直径
                lit = self._palette_at(i / (_BAR_COUNT - 1) * _WAVE_SPAN - self._frame * _WAVE_FLOW * 2)
                x = self._wave_x0 + (i + 0.5) * step
                prims.append((x, self._mid_y, x, self._mid_y, d, self._lerp(_DOT_DIM, lit, k)))
        else:
            p = self._phase0 + self._frame * _WAVE_SPEED
            # 整体响度：优先真实麦克风电平（平滑：起快落慢），
            # 拿不到新鲜电平时回退到合成的“说话般”起伏
            raw, fresh = _read_mic_level()
            if fresh:
                # 噪声门：减掉底噪，静音时归零，避免没说话也在动
                eff = raw - self._gate
                eff = eff if eff > 0.0 else 0.0
                # 本地改: 自动增益 —— 离麦克风远时电平小, 固定增益下波纹几乎不动.
                # 按近期峰值归一化 (峰值每帧衰减 3%, 约 1 秒半衰), 增益最多放大到配置值的 6 倍, 免得放大底噪
                self._peak = max(eff, self._peak * 0.97)
                gain = min(max(self._gain, 0.8 / self._peak), self._gain * 6) if self._peak > 0 else self._gain
                target = min(1.0, eff * gain) ** _LEVEL_GAMMA
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
                    prims.append((x, self._mid_y, x, self._mid_y, d, _DOT_DIM))
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

        self._after_id = self.window.after(_FRAME_MS, self._tick)

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
        try:
            self._proc_timeout_ms = max(_PROC_TIMEOUT_MS, int(new_text.split(':', 1)[1]))
        except (IndexError, ValueError):
            pass
        self._mode = 'processing'

    def set_text(self, new_text: str) -> None:     # pragma: no cover - 兼容占位
        pass
