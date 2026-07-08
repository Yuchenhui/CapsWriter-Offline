# coding: utf-8
"""
录音状态悬浮提示模块

在录音期间于屏幕正中显示一个深色圆角胶囊（REC 红点 + 「正在聆听」+ 动态声波条），
松开快捷键时立即消失。胶囊自身在 Tk 线程内驱动动画，本模块只负责开/关。

与控制台的 spinner 动画（core.tools.my_status.Status）平行，互不影响，
可通过 config_client.ClientConfig.show_recording_toast 开关。
"""

from __future__ import annotations

from typing import Optional

from config_client import ClientConfig as Config
from . import logger


_LABEL = '正在聆听'


class RecordingToast:
    """录音状态悬浮提示

    每个快捷键任务持有一个独立实例，与 Status 控制台动画一一对应。
    """

    def __init__(self) -> None:
        self._manager = None
        self._msg_id: Optional[str] = None

    def start(self) -> None:
        """开始显示悬浮提示（若已显示则忽略）"""
        if not Config.show_recording_toast:
            return
        if self._msg_id is not None:
            return

        try:
            from core.ui.toast import ToastMessageManager, ToastMessage
            from core.ui.recording_level import reset as reset_level

            reset_level()  # 清掉上次录音残留的电平
            self._manager = ToastMessageManager()
            msg = ToastMessage(
                text=_LABEL,
                streaming=True,            # 常驻，不自动消失，直到 close
                window_type='recording',
            )
            self._msg_id = self._manager.add_message(msg)
            logger.debug('录音悬浮提示已显示')
        except Exception as e:
            logger.error(f'显示录音悬浮提示失败: {e}', exc_info=True)
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
            self._manager = None
            self._msg_id = None
            logger.debug('录音悬浮提示已关闭')
