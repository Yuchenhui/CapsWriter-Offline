# coding: utf-8
"""
音频流管理模块

提供 AudioStreamManager 类用于管理音频输入流，包括流的创建、
启动、停止和设备检测。
"""

from __future__ import annotations

import sys
import time
import threading
from typing import TYPE_CHECKING, Optional

import numpy as np
import sounddevice as sd

from core.client.state import console
from . import logger

if TYPE_CHECKING:
    from core.client.state import ClientState
    from ..app import CapsWriterClient



class AudioStreamManager:
    """
    音频流管理器

    负责管理音频输入流的生命周期，包括：
    - 检测和选择音频设备
    - 创建和启动音频流
    - 处理音频数据回调
    - 流的重启和关闭

    Attributes:
        state: 客户端状态实例
        sample_rate: 采样率（默认 48000Hz）
        block_duration: 每个数据块的时长（秒，默认 0.05s）
    """

    SAMPLE_RATE = 48000
    BLOCK_DURATION = 0.05  # 50ms

    def __init__(self, app: CapsWriterClient):
        """
        初始化音频流管理器

        Args:
            app: 客户端 App 实例
        """
        self.app = app
        self._channels = 1
        self._running = False  # 标志是否应该运行
        self._last_cb = 0.0    # 本地改 F5: 最近一次音频回调的时刻 (monotonic), 判断流是否"活着但不送数据"
        self._lock = threading.RLock()   # 本地改 F5: 结束回调 / 按键唤醒 / 默认设备跟随 可能同时重开

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status: sd.CallbackFlags
    ) -> None:
        """
        音频数据回调函数

        当音频流接收到新数据时调用，将数据放入异步队列中。
        """
        self._last_cb = time.monotonic()
        # 只在录音状态时处理数据
        if not self.state.recording:
            return

        import asyncio

        # 只读采样：算一下本块的 RMS 电平，喂给悬浮胶囊做真实波形（不影响录音/识别）
        try:
            from core.ui.recording_level import set_level
            set_level(float(np.sqrt(np.mean(np.square(indata)))))
        except Exception:
            pass

        # 将数据放入队列
        if self.app.loop and self.state.queue_in:
            asyncio.run_coroutine_threadsafe(
                self.state.queue_in.put({
                    'type': 'data',
                    'time': time.time(),
                    'data': indata.copy(),
                }),
                self.app.loop
            )

    def _on_stream_finished(self) -> None:
        """音频流结束回调"""
        if not threading.main_thread().is_alive():
            return
        if not self._running:
            return

        logger.info("音频流意外结束，正在尝试重启...")
        # 本地改 F5 (参考上游 PR #460): 这里在 PortAudio 的回调线程里, 不能就地关流重建 (还会卸载正在执行回调的库),
        # 交给独立线程做
        threading.Thread(target=self.reopen, daemon=True, name='mic-reopen').start()

    def is_stale(self, max_age: float = 1.0) -> bool:
        """本地改 F5: 流标着在运行, 但超过 max_age 秒没有任何回调 (锁屏/睡眠/驱动重置后常见), 视为失效"""
        return self._running and time.monotonic() - self._last_cb > max_age

    def start(self) -> Optional[sd.InputStream]:
        """
        启动音频流

        Returns:
            创建的音频输入流，如果失败返回 None
        """
        with self._lock:
            return self._start_locked()

    def _start_locked(self) -> Optional[sd.InputStream]:
        if self._running:
            logger.debug("音频流已在运行，跳过启动")
            return self.state.stream

        # 检测音频设备
        try:
            device = sd.query_devices(kind='input')
            self._channels = min(2, device['max_input_channels'])
            device_name = device.get('name', '未知设备')
            console.print(
                f'使用默认音频设备：[italic]{device_name}，声道数：{self._channels}',
                end='\n\n'
            )
            logger.info(f"找到音频设备: {device_name}, 声道数: {self._channels}")
        except UnicodeDecodeError:
            logger.warning("无法获取音频设备名称（编码问题）")
        except sd.PortAudioError:
            # 本地改: 原来 input() 等回车再退出; 无窗口运行 stdin 不可用会直接崩. 改为返回 None, 下次按键/换设备时再试
            logger.error("未找到麦克风设备, 稍后重试 (插上麦克风或在系统里选默认录音设备)")
            return None

        # 创建音频流
        try:
            stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=int(self.BLOCK_DURATION * self.SAMPLE_RATE),
                device=None,
                dtype="float32",
                channels=self._channels,
                callback=self._audio_callback,
                finished_callback=self._on_stream_finished,
            )
            stream.start()

            self.state.stream = stream
            self._last_cb = time.monotonic()   # 刚开的流给 1 个判定周期, 别被当成失效
            self._running = True
            logger.debug(
                f"音频流已启动: 采样率={self.SAMPLE_RATE}, "
                f"块大小={int(self.BLOCK_DURATION * self.SAMPLE_RATE)}"
            )
            return stream

        except sd.PortAudioError as e:
            logger.error(f"创建音频流失败: {e}", exc_info=True)
            if '-9999' in str(e):
                console.print("""
[bold red]检测到麦克风被占用或权限异常（错误码 -9999）[/bold red]
请尝试以下解决方案：

  1. 设置 > 隐私和安全性 > 麦克风，将「允许桌面应用访问麦克风」打开
  2. 状态栏右下角音量图标 > 右键菜单 > 声音 > 麦克风的属性，关闭「允许应用程序独占控制该设备」
  3. 状态栏右下角音量图标 > 右键菜单 > 声音 > 麦克风的属性，关闭「增强效果」
""")
            return None
        except Exception as e:
            logger.error(f"创建音频流失败: {e}", exc_info=True)
            return None

    def stop(self) -> None:
        """停止音频流"""
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        if not self._running:
            return

        self._running = False  # 标记为停止
        if self.state.stream is not None:
            try:
                self.state.stream.close()
                logger.debug("音频流已停止")
            except Exception as e:
                logger.debug(f"停止音频流时发生错误: {e}")
            finally:
                self.state.stream = None

    def reopen(self) -> Optional[sd.InputStream]:
        """
        重新启动音频流

        Returns:
            新创建的音频输入流
        """
        with self._lock:
            logger.info("正在重启音频流...")

            # 停止旧流
            self._stop_locked()

            # 重新初始化 PortAudio，更新设备列表.
            # 本地改 F5 (参考上游 PR #460): 不再 dlclose/dlopen 卸载共享库 —— 旧流的 CFFI 回调可能仍引用它
            try:
                sd._terminate()
                sd._initialize()
            except Exception as e:
                logger.warning(f"重载 PortAudio 时发生警告: {e}")

            # 等待设备稳定
            time.sleep(0.1)

            # 启动新流
            return self._start_locked()
