# coding: utf-8
"""
录音状态悬浮提示模块

在录音期间于屏幕中下部显示一个深色圆角胶囊（REC 红点 + 「正在聆听」+ 动态声波条）；
松开快捷键后切换为「正在转文字」处理态，直到识别结果开始可见输出才关闭。
胶囊自身在 Tk 线程内驱动动画，本模块负责开/关/状态切换。

模块级维护"当前活动实例"（同一时刻至多一个胶囊在屏）：
    松开按键后 ShortcutTask.finish 调用实例方法 processing() 切到处理态；
    识别链路在首个可见输出时调用模块级 close_active() 关闭当前胶囊。
    close_active() 线程安全、幂等，无活动实例时为 no-op。

与控制台的 spinner 动画（core.tools.my_status.Status）平行，互不影响，
可通过 config_client.ClientConfig.show_recording_toast 开关。
"""

from __future__ import annotations

import threading
from typing import Optional

from config_client import ClientConfig as Config
from . import logger


_LABEL = '正在聆听'

# 当前活动实例注册表（同一时刻至多一个胶囊在屏）
_active_lock = threading.Lock()
_active: Optional['RecordingToast'] = None


def close_active() -> None:
    """关闭当前活动胶囊；无活动实例时 no-op"""
    with _active_lock:
        inst = _active
    if inst is not None:
        inst.stop()


def _register(inst: 'RecordingToast') -> None:
    global _active
    with _active_lock:
        _active = inst


def _unregister(inst: 'RecordingToast') -> None:
    global _active
    with _active_lock:
        if _active is inst:
            _active = None


class RecordingToast:
    """录音状态悬浮提示

    每个快捷键任务持有一个独立实例，与 Status 控制台动画一一对应。
    """

    def __init__(self) -> None:
        self._manager = None
        self._msg_id: Optional[str] = None

    def start(self) -> None:
        """开始显示悬浮提示（先关掉可能残留的上一个胶囊）"""
        if not Config.show_recording_toast:
            return
        # 快速连续听写时，上一轮的「转写中」胶囊可能还在屏上，先关旧再开新
        close_active()
        if self._msg_id is not None:
            # close_active 只关注册过的实例；自身残留时兜底自关
            self.stop()

        try:
            from core.ui.toast import ToastMessageManager, ToastMessage
            from core.ui.recording_level import reset as reset_level

            reset_level()  # 清掉上次录音残留的电平
            self._manager = ToastMessageManager()
            msg = ToastMessage(
                text=_LABEL,
                streaming=True,            # 常驻，不自动消失，直到 close
                window_type='recording',
                stop_callback=self._on_window_gone,  # 胶囊超时自毁时回收注册状态
            )
            self._msg_id = self._manager.add_message(msg)
            _register(self)
            logger.debug('录音悬浮提示已显示')
        except Exception as e:
            logger.error(f'显示录音悬浮提示失败: {e}', exc_info=True)
            self._manager = None
            self._msg_id = None

    def processing(self, duration: float = 0.0) -> None:
        """切换到「正在转文字」处理态（未显示则忽略，保持注册以便后续 close_active）

        recording 窗口对任意 update_toast 文本都解释为状态切换（展示文案由窗口自持）。
        字符串携带按录音时长缩放的超时毫秒（'processing:<ms>'）：转录时延与录音
        时长成正比（实测约 0.5 倍实时），固定 15s 兜底会在长录音的识别结果到达前
        误杀胶囊，故按 0.8×时长+10s 放宽；窗口端保证不低于默认 15s。

        Args:
            duration: 本次录音时长（秒），0 表示未知（超时保持默认）。
        """
        if self._msg_id is None or self._manager is None:
            return
        try:
            timeout_ms = int(duration * 800) + 10_000
            self._manager.update_toast(self._msg_id, f'processing:{timeout_ms}')
            logger.debug(f'录音悬浮提示已切换为正在转文字 (超时兜底 {timeout_ms}ms)')
        except Exception as e:
            logger.error(f'切换转写状态失败: {e}', exc_info=True)

    def _on_window_gone(self) -> None:
        """胶囊窗口自行销毁（如超时自关）时的回调。

        只回收模块级注册与本地句柄，不再回调 manager.close_toast（窗口已在销毁中，
        再调只会得到「未找到消息 ID」警告）。可能由 Tk 线程调用，_unregister 自带锁。
        """
        _unregister(self)
        self._manager = None
        self._msg_id = None

    def stop(self) -> None:
        """关闭悬浮提示（若未显示则忽略）"""
        if self._msg_id is None:
            return
        try:
            if self._manager is not None:
                self._manager.close_toast(self._msg_id)
        except Exception as e:
            logger.error(f'关闭录音悬浮提示失败: {e}', exc_info=True)
        finally:
            _unregister(self)
            self._manager = None
            self._msg_id = None
            logger.debug('录音悬浮提示已关闭')
