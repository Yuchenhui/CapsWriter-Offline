# coding: utf-8
"""
快捷键任务模块

管理单个快捷键的录音任务状态
"""

from __future__ import annotations
import asyncio
import time
from threading import Event
from typing import TYPE_CHECKING, Optional

from . import logger
from core.tools.my_status import Status
from core.client.ui.recording_toast import RecordingToast
from core.tools.window_focus import activate_window_under_cursor
from core.client.audio import speaker_mute, idle_release
from config_client import ClientConfig as _Cfg
import threading as _threading
 
if TYPE_CHECKING:
    from core.client.shortcut.shortcut_config import Shortcut
    from core.client.state import ClientState
    from core.client.audio.recorder import AudioRecorder
    from core.client.app import CapsWriterClient



class ShortcutTask:
    """
    单个快捷键的录音任务

    跟踪每个快捷键独立的录音状态，防止互相干扰。
    """

    def __init__(self, app: CapsWriterClient, shortcut: Shortcut, recorder_class=None):
        """
        初始化快捷键任务

        Args:
            app: 客户端 App 实例
            shortcut: 快捷键配置
            recorder_class: AudioRecorder 类（可选，用于延迟导入）
        """
        self.app = app
        self.shortcut = shortcut
        self._recorder_class = recorder_class

        # 任务状态
        self.task: Optional[asyncio.Future] = None
        self.recording_start_time: float = 0.0
        self.is_recording: bool = False
        self.capped: bool = False       # 到时长上限自动结束后: 不再开录, 收到松开才解除
        self._end_lock = _threading.Lock()   # 松开 / 守护线程可能同时结束录音, 只结束一次

        # hold_mode 状态跟踪
        self.pressed: bool = False
        self.released: bool = True
        self.event: Event = Event()

        # 线程池（用于 countdown）
        self.pool = None

        # 录音状态动画
        self._status = Status('开始录音', spinner='point')

        # 录音状态悬浮提示（屏幕浮动条，可通过配置关闭）
        self._rec_toast = RecordingToast()

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    def _get_recorder(self) -> AudioRecorder:
        """获取 AudioRecorder 实例"""
        if self._recorder_class is None:
            from core.client.audio.recorder import AudioRecorder
            self._recorder_class = AudioRecorder
        return self._recorder_class(self.app)

    def launch(self) -> None:
        """启动录音任务"""
        logger.info(f"[{self.shortcut.key}] 触发：开始录音")

        # 先把鼠标下的窗口切到前台, 结果就粘贴到那里 (替代 AHK 的 ~RAlt)
        from core.client import key_trace
        _t0, _a0 = time.perf_counter(), key_trace.ralt()
        try:
            _path = activate_window_under_cursor()
        except Exception as e:
            _path = f'error {e!r}'
            logger.debug(f"激活鼠标下窗口出错: {e}")
        key_trace.push('step', f'切前台 {_path} {(time.perf_counter() - _t0) * 1000:.1f}ms RAlt {_a0}->{key_trace.ralt()}')
        idle_release.wake(self.app)   # 麦克风若已闲置释放, 后台重新打开 (约 0.5s)

        # 记录开始时间
        self.recording_start_time = time.time()
        self.is_recording = True
        cap = float(getattr(_Cfg, 'max_record_sec', 0) or 0)
        from core.ui import toast_recording
        toast_recording.cap_deadline = self.recording_start_time + cap if cap else 0.0
        _threading.Thread(target=self._guard, args=(self.recording_start_time, cap), daemon=True, name='rec-guard').start()

        # 将开始标志放入队列
        asyncio.run_coroutine_threadsafe(
            self.state.queue_in.put({'type': 'begin', 'time': self.recording_start_time, 'data': None}),
            self.app.loop
        )

        # 更新录音状态
        self.state.start_recording(self.recording_start_time)

        # 打印动画：正在录音
        self._status.start()
        self._rec_toast.start()

        # 音箱静音: 等过了短按阈值再静, 否则每次短按右 Alt 声音都会断一下
        if getattr(_Cfg, 'mute_speaker_while_recording', False):
            self._mute_timer = _threading.Timer(_Cfg.threshold, lambda: self.is_recording and speaker_mute.mute())
            self._mute_timer.daemon = True
            self._mute_timer.start()

        # 启动识别任务
        recorder = self._get_recorder()
        self.task = asyncio.run_coroutine_threadsafe(
            recorder.record_and_send(),
            self.app.loop,
        )

    def _unmute(self) -> None:
        t = getattr(self, '_mute_timer', None)
        if t:
            t.cancel()
        from core.client import key_trace
        _t0 = time.perf_counter()
        speaker_mute.restore()   # 没被静音过时什么都不做
        key_trace.push('step', f'恢复静音 {(time.perf_counter() - _t0) * 1000:.1f}ms RAlt {key_trace.ralt()}')

    @staticmethod
    def _clear_deadline() -> None:
        from core.ui import toast_recording
        toast_recording.cap_deadline = 0.0

    def _guard(self, start: float, cap: float) -> None:
        """录音守护 (独立线程, 不占键盘钩子): 到时长上限自动结束. 松开事件丢了 (09-26 17:45 录到 328 秒) 也靠它兜底.
        曾用"自动重复停了 1.2 秒 = 已松开"判断, 但本机键盘按住时重复会中断数秒 (09-26 19:45 按着被误判松开), 已删"""
        if not cap:
            return
        while self.is_recording and self.recording_start_time == start:
            time.sleep(0.1)
            if time.time() - start < cap:
                continue
            if self.is_recording and self.recording_start_time == start:
                self.capped = True
                logger.warning(f'[{self.shortcut.key}] 到时长上限 {cap:g} 秒, 自动结束 (松开前不再开录)')
                self.finish()
            return

    def cancel(self) -> None:
        """取消录音任务（时间过短）"""
        logger.debug(f"[{self.shortcut.key}] 取消录音任务（时间过短）")

        with self._end_lock:
            if not self.is_recording:
                return
            self.is_recording = False
        self._clear_deadline()
        self._unmute()
        idle_release.schedule(self.app)
        self.state.stop_recording()
        self._status.stop()
        self._rec_toast.stop()

        self.task.cancel()
        self.task = None

    def finish(self) -> None:
        """完成录音任务"""
        with self._end_lock:
            if not self.is_recording:
                return
            self.is_recording = False
        logger.info(f"[{self.shortcut.key}] 释放：完成录音")
        self._clear_deadline()
        self._unmute()
        idle_release.schedule(self.app)
        duration = self.state.stop_recording()
        self._status.stop()
        # 松键后胶囊不关闭，原地切换「转写中」；由 ResultProcessor/LLM 输出时关闭
        # 传录音时长供胶囊按比例放宽超时兜底（转录时延与录音时长成正比）
        self._rec_toast.processing(duration)

        asyncio.run_coroutine_threadsafe(
            self.state.queue_in.put({
                'type': 'finish',
                'time': time.time(),
                'data': None
            }),
            self.app.loop
        )

        # 执行 restore（可恢复按键 + 非阻塞模式）
        # 阻塞模式下按键不会发送到系统，状态不会改变，不需要恢复
        if self.shortcut.is_toggle_key() and not self.shortcut.suppress:
            self._restore_key()

    def _restore_key(self) -> None:
        """恢复按键状态（防自捕获逻辑由 ShortcutManager 处理）"""
        # 通知管理器执行 restore
        # 防自捕获：管理器会设置 flag 再发送按键
        manager = self._manager_ref()
        if manager:
            logger.debug(f"[{self.shortcut.key}] 自动恢复按键状态 (suppress={self.shortcut.suppress})")
            manager.schedule_restore(self.shortcut.key)
        else:
            logger.warning(f"[{self.shortcut.key}] manager 引用丢失，无法 restore")
