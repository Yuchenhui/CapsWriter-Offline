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
_PILL_BG = '#14141a'       # 胶囊底色（近黑，微偏冷以配青）
_PILL_STROKE = '#4a4a54'   # 玻璃边缘：半透明背景下用一道细边定住轮廓
_TEXT_FG = '#f5f5f7'
_DOT_HI = '#ff453a'        # REC 红点（亮）
_DOT_LO = '#5c1a16'        # REC 红点（暗，用于呼吸插值）
_ALPHA = 0.88              # 整体通透度（越小越透，文字仍需可读）

# 签名元素：密集声条频谱（白→青渐变），中间高两侧低，带说话般起伏 + 横向流动
_WAVE_C1 = '#f5f5f7'       # 声条左端色（近白）
_WAVE_C2 = '#35e0d0'       # 声条右端色（青，签名色）
_WAVE_W = 84               # 声条区域宽度（像素）
_WAVE_AMP = 11             # 声条半振幅（像素，满幅约 2×）
_WAVE_SPEED = 0.17         # 相位推进速度（每帧）
_BAR_COUNT = 15            # 声条数量
_BAR_W = 3                 # 单条宽度（像素，圆头）
_BAR_MIN_H = 2             # 静止时的最小半高，避免消失

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
_DOT_R = 4.5               # 红点半径
_GAP_DOT_TEXT = 12
_GAP_TEXT_WAVE = 20

_BOTTOM_MARGIN = 16        # 胶囊底边距任务栏（工作区底部）的像素间距

# 「正在转文字」处理态（松开按键后等待识别结果期间）
_PROC_LABEL = '正在转文字'
_PROC_TIMEOUT_MS = 15_000  # 处理态超时自关（毫秒，服务端假死/静默丢结果兜底）

# 处理态动效：骨架文字微光——一行宽窄错落的占位短横（像即将显影的文字），
# 青白色微光从左向右循环扫过。与聆听态共用 3px 圆头笔触，竖条(声音)→横线(文字)，
# 不含任何「正在拾音」语义。
_DASH_WIDTHS = (14, 22, 10, 18)   # 各短横宽度（宽窄错落，模拟一行字的节奏）
_DASH_GAP = 5                     # 短横间距
_DASH_W = 3                       # 笔触粗细（与声条一致，圆头）
_DASH_BASE = '#3f3f4a'            # 骨架底色（暗灰，静候显影）
_DASH_HI = '#e9fffb'              # 扫光峰值色（青白近白，延续签名青）
_DASH_SWEEP_SPEED = 3.0           # 扫光速度（像素/帧，~25fps 下一轮约 1.8s）
_DASH_SIGMA = 26                  # 扫光半径（像素，越大光晕越宽越柔）
_DASH_SWELL = 1.8                 # 扫光经过时短横加粗量（像素，微呼吸感）

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
        self._text = text or '正在聆听'
        self._font_family = font_family if font_family else DEFAULT_FONT_FAMILY
        self._font_size = font_size or _FONT_SIZE
        self._frame = 0
        self._alpha = 0.0
        self._target_alpha = _target_alpha()   # 淡入目标不透明度（读配置）
        self._after_id: Optional[str] = None
        self._level = 0.0              # 平滑后的真实电平（0~1）
        self._gain = _level_gain()     # 灵敏度（读配置，创建时定）
        self._gate = _level_gate()     # 噪声门（读配置，创建时定）
        # 波形相位起点随机，避免每次录音从同一形状开始
        self._phase0 = random.uniform(0, 6.283)
        # 预算好每根声条的白→青颜色，避免每帧重复插值
        self._wave_colors = [
            self._lerp(_WAVE_C1, _WAVE_C2, i / (_BAR_COUNT - 1))
            for i in range(_BAR_COUNT)
        ]
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
        try:
            self.window.attributes('-transparentcolor', _CHROMA)
            self.window.attributes('-alpha', 0.0)   # 从透明开始淡入
        except tk.TclError:
            # 平台不支持透明属性时降级为不透明
            self._alpha = self._target_alpha

        self.window.configure(bg=_CHROMA)
        self.window.pack_propagate(False)

        # 测量文本，计算胶囊尺寸
        from tkinter import font as tkfont
        self._font = tkfont.Font(family=self._font_family, size=self._font_size)
        text_w = self._font.measure(self._text)
        self._w = int(round(_PAD_X + _DOT_R * 2 + _GAP_DOT_TEXT + text_w
                            + _GAP_TEXT_WAVE + _WAVE_W + _PAD_X))
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
        self.window.geometry(f'{self._w}x{self._h}+{x}+{y}')

        # 预存布局坐标
        self._dot_cx = _PAD_X + _DOT_R
        self._text_x = self._dot_cx + _DOT_R + _GAP_DOT_TEXT
        self._wave_x0 = self._text_x + text_w + _GAP_TEXT_WAVE
        self._mid_y = self._h / 2

        self._draw_static()
        self.window.deiconify()
        self._tick()

    # -- 绘制 --------------------------------------------------------------
    def _draw_static(self) -> None:
        """绘制不变的部分：圆角胶囊底（含玻璃细边）+ 文案"""
        # 内缩 1px，让描边不被画布裁掉；细边在半透明背景下定住胶囊轮廓
        r = self._h / 2 - 1
        self._round_rect(
            1, 1, self._w - 1, self._h - 1, r,
            fill=_PILL_BG, outline=_PILL_STROKE, width=1,
        )
        self.canvas.create_text(
            self._text_x, self._mid_y, text=self._text, anchor='w',
            fill=_TEXT_FG, font=self._font,
        )

    def _round_rect(self, x1, y1, x2, y2, r, **kw) -> None:
        pts = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        self.canvas.create_polygon(pts, smooth=True, **kw)

    def _tick(self) -> None:
        """每帧：淡入 + 红点呼吸 + 波形跳动"""
        try:
            if not self.window.winfo_exists():
                return
        except tk.TclError:
            return

        self._frame += 1

        # 状态切换：update_text 可能从任意线程置 _mode，重绘只在本 Tk 线程做
        if self._mode != self._applied_mode:
            self._applied_mode = self._mode
            self._text = _PROC_LABEL
            self._proc_frames = 0
            # 处理态重新布局：无 REC 点，「正在转文字」+ 骨架短横整体居中
            text_w = self._font.measure(self._text)
            dash_span = sum(_DASH_WIDTHS) + _DASH_GAP * (len(_DASH_WIDTHS) - 1)
            total_w = text_w + _GAP_TEXT_WAVE + dash_span
            self._text_x = (self._w - total_w) / 2
            self._dash_x0 = self._text_x + text_w + _GAP_TEXT_WAVE
            self._dash_span = dash_span
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
            try:
                self.window.attributes('-alpha', self._alpha)
            except tk.TclError:
                pass

        self.canvas.delete('dyn')

        # REC 红点呼吸——仅聆听态；处理态不保留任何「正在拾音」语义的元素
        if not processing:
            pulse = (math.sin(self._frame * 0.16) + 1) / 2      # 0..1
            dot_color = self._lerp(_DOT_LO, _DOT_HI, 0.35 + 0.65 * pulse)
            rr = _DOT_R + pulse * 1.2
            self.canvas.create_oval(
                self._dot_cx - rr, self._mid_y - rr,
                self._dot_cx + rr, self._mid_y + rr,
                fill=dot_color, outline='', tags='dyn',
            )

        # 动效区：处理态 = 骨架文字微光（占位短横 + 循环扫光，像文字即将显影）；
        #         聆听态 = 密集声条频谱（白→青渐变，中间高两侧低，横向流动）
        if processing:
            # 扫光位置在 [-σ, span+σ] 循环，出场入场都有淡出余量
            cycle = self._dash_span + 2 * _DASH_SIGMA
            sweep = (self._proc_frames * _DASH_SWEEP_SPEED) % cycle - _DASH_SIGMA
            x = self._dash_x0
            for w in _DASH_WIDTHS:
                # 短横中心的「扫光轨道」相对坐标（sweep 是相对 dash_x0 的 0~span）
                rel_cx = x - self._dash_x0 + w / 2
                # 距扫光中心越近越亮（三角衰减再 1.5 次方，光晕柔和）
                k = max(0.0, 1.0 - abs(rel_cx - sweep) / _DASH_SIGMA) ** 1.5
                color = self._lerp(_DASH_BASE, _DASH_HI, k)
                self.canvas.create_line(
                    x, self._mid_y, x + w, self._mid_y,
                    width=_DASH_W + _DASH_SWELL * k, fill=color,
                    capstyle=tk.ROUND, tags='dyn',
                )
                x += w + _DASH_GAP
        else:
            p = self._phase0 + self._frame * _WAVE_SPEED
            # 整体响度：优先真实麦克风电平（平滑：起快落慢），
            # 拿不到新鲜电平时回退到合成的“说话般”起伏
            raw, fresh = _read_mic_level()
            if fresh:
                # 噪声门：减掉底噪，静音时归零，避免没说话也在动
                eff = raw - self._gate
                eff = eff if eff > 0.0 else 0.0
                target = min(1.0, eff * self._gain) ** _LEVEL_GAMMA
                k = _LEVEL_ATTACK if target > self._level else _LEVEL_DECAY
                self._level += (target - self._level) * k
                speech = _LEVEL_FLOOR + (1.0 - _LEVEL_FLOOR) * self._level
                # 纹理深度随响度：安静时几乎静止，说话时才活跃
                depth = 0.15 + 0.85 * self._level
            else:
                speech = 0.32 + 0.68 * abs(math.sin(p * 0.9))
                depth = 1.0
            step = _WAVE_W / _BAR_COUNT
            for i in range(_BAR_COUNT):
                t = (i + 0.5) / _BAR_COUNT
                env = math.sin(math.pi * t)                       # 中间高、两侧低
                wave = 0.5 + 0.5 * math.sin(t * 11 - p * 3.2) * math.sin(t * 4 + p * 1.6)
                detail = (1.0 - depth) + depth * wave             # depth 小→趋于静止
                half = _WAVE_AMP * env * speech * detail
                if half < _BAR_MIN_H:
                    half = _BAR_MIN_H
                x = self._wave_x0 + (i + 0.5) * step
                self.canvas.create_line(
                    x, self._mid_y - half, x, self._mid_y + half,
                    width=_BAR_W, fill=self._wave_colors[i],
                    capstyle=tk.ROUND, tags='dyn',
                )

        self._after_id = self.window.after(_FRAME_MS, self._tick)

    # -- 工具 --------------------------------------------------------------
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
