# coding: utf-8
"""
音频录制模块

提供 AudioRecorder 类用于管理录音会话，包括开始录音、
发送音频数据到服务端、结束录音等功能。
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from typing import TYPE_CHECKING, Optional

import numpy as np
import websockets

from config_client import ClientConfig as Config
from core.tools.terms import load_terms
from core.client.state import console
from core.client.audio.file_manager import AudioFileManager
from core.client.connection import WebSocketManager
from core.protocol import AudioMessage
from . import logger

if TYPE_CHECKING:
    from core.client.state import ClientState
    from core.client.app import CapsWriterClient

# 日志记录器


def _window_desc() -> str:
    """前台窗口 "进程名 | 标题" (标题截 80 字), 失败返回空串"""
    try:
        from core.tools.window_detector import get_active_window_info
        w = get_active_window_info()
        return f"{w.get('process_name', '')} | {(w.get('title') or '')[:80]}".strip(' |')
    except Exception:
        return ''


class AudioRecorder:
    """
    音频录制器
    
    管理一次完整的录音会话，包括：
    - 从音频流接收数据
    - 可选地保存到本地文件
    - 将音频数据发送到识别服务端
    """
    
    def __init__(self, app: CapsWriterClient):
        """
        初始化录制器
        
        Args:
            app: 客户端 App 实例
        """
        self.app = app
        self.task_id: Optional[str] = None
        self._file_manager: Optional[AudioFileManager] = None
        self._start_time: float = 0.0
        self._duration: float = 0.0
        self._cache: list = []

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    @property
    def _ws_manager(self) -> WebSocketManager:
        """快捷访问桥接到 app.ws"""
        return self.app.ws
    
    async def _send_message(self, message: AudioMessage) -> None:
        """发送消息到服务端"""
        if not self._ws_manager.is_connected:
            if message.is_final:
                self.state.pop_audio_file(message.task_id)
                console.print('    服务端未连接，无法发送\n')
                logger.warning("服务端未连接，无法发送音频数据")
            return
        
        # 使用 WebSocketManager 发送协议消息
        success = await self._ws_manager.send(message)
        if not success and message.is_final:
            self.state.pop_audio_file(message.task_id)
            # 具体错误日志由 WebSocketManager 记录
    
    async def record_and_send(self) -> None:
        """
        录音并发送数据

        从队列中读取音频数据，保存到文件（如果启用），
        并发送到服务端进行识别。
        """
        try:
            # 生成唯一任务 ID
            self.task_id = str(uuid.uuid1())
            logger.debug(f"创建录音任务，任务ID: {self.task_id}")

            self._start_time = 0.0
            self._duration = 0.0
            self._cache = []
            self._sumsq, self._nsamp, self._peak = 0.0, 0, 0.0   # 诊断: 本句音量
            self._blk_db = []   # 每 50ms 块的电平, 算底噪 / 信噪比
            from core.client.audio.decimate import Decimator3
            self._dec = Decimator3()   # 本地改: 抗混叠降采样, 每段录音一个实例 (跨块保留滤波状态)
            
            # 音频文件管理
            file_path = None
            if Config.save_audio:
                self._file_manager = AudioFileManager()
            
            # 从队列读取数据
            while task := await self.state.queue_in.get():
                self.state.queue_in.task_done()
                
                if task['type'] == 'begin':
                    self._start_time = task['time']
                    self._window = _window_desc()   # 本地改: 此时鼠标下窗口已切到前台, 就是要粘贴的目标
                    logger.debug(f"录音开始，时间戳: {self._start_time}")
                    
                elif task['type'] == 'data':
                    _d = task['data']
                    if _d.size:
                        self._sumsq += float(np.sum(np.square(_d, dtype=np.float64)))
                        self._nsamp += int(_d.size)
                        self._peak = max(self._peak, float(np.max(np.abs(_d))))
                        self._blk_db.append(10 * np.log10(float(np.mean(np.square(_d, dtype=np.float64))) + 1e-12))
                    # 在阈值之前积攒音频数据 (本地改 F6: 开了静音门限时多攒到 silence_gate_hold 秒, 松开时整句判断)
                    _hold = Config.silence_gate_hold if getattr(Config, 'silence_rms_gate', 0) else 0
                    if task['time'] - self._start_time < max(Config.threshold, _hold):
                        self._cache.append(task['data'])
                        continue
                    
                    # 创建音频文件
                    if Config.save_audio and self._file_manager and file_path is None:
                        file_path, _ = self._file_manager.create(
                            task['data'].shape[1],
                            self._start_time
                        )
                        self.state.register_audio_file(self.task_id, file_path)
                        logger.debug(f"创建音频文件: {file_path}")
                    
                    # 获取音频数据
                    if self._cache:
                        data = np.concatenate(self._cache)
                        self._cache.clear()
                    else:
                        data = task['data']
                    
                    # 保存音频至本地文件
                    self._duration += len(data) / 48000
                    if Config.save_audio and self._file_manager:
                        self._file_manager.write(data)
                    
                    # 发送音频数据用于识别
                    message = AudioMessage(
                        task_id=self.task_id,
                        source='mic',
                        data=base64.b64encode(
                            self._dec.process(data).tobytes()
                        ).decode('utf-8'),
                        is_final=False,
                        time_start=self._start_time,
                        seg_duration=Config.mic_seg_duration,
                        seg_overlap=Config.mic_seg_overlap,
                        context=load_terms() or Config.context,
                        polish=Config.polish,
                        window=getattr(self, '_window', ''),
                        language=Config.language,
                    )
                    asyncio.create_task(self._send_message(message))
                    
                elif task['type'] == 'finish':
                    if self._nsamp:
                        _db = lambda v: 20 * np.log10(v) if v > 0 else -120.0
                        _b = [x for x in self._blk_db if x > -90]   # 排除纯数字静音块 (麦克风闲置释放后重开的头几块), 否则底噪虚低
                        _noise, _voice = (np.percentile(_b, 10), np.percentile(_b, 90)) if _b else (0, 0)
                        logger.info(f"本句音量: 平均 {_db((self._sumsq / self._nsamp) ** 0.5):.1f} dBFS, "
                                    f"峰值 {_db(self._peak):.1f} dBFS, 底噪 {_noise:.1f} dBFS, 说话 {_voice:.1f} dBFS, "
                                    f"信噪比 {_voice - _noise:.0f} dB, 任务ID: {self.task_id}")
                    # 本地改 (审计 F6): 整句平均音量低于门限且一段都还没发 -> 丢弃.
                    # 实测 2026-09-23: 没说话时 Qwen3 会把术语表 (context) 念成一段"识别结果" (平均 -53.5 / -50.1 dBFS)
                    _gate = float(getattr(Config, 'silence_rms_gate', 0) or 0)
                    _rms = (self._sumsq / self._nsamp) ** 0.5 if self._nsamp else 0.0
                    if _gate and _rms < _gate and self._duration == 0.0:
                        logger.info(f"录音太安静 (平均 {20 * np.log10(max(_rms, 1e-6)):.1f} dBFS < 门限), 不送识别, 任务ID: {self.task_id}")
                        console.print('    录音太安静, 未识别')
                        self._cache.clear()
                        if Config.save_audio and self._file_manager:
                            self._file_manager.finish()
                        from core.client.ui.recording_toast import close_active
                        close_active()
                        break

                    # 如果有缓存的数据未发送，先发送缓存
                    if self._cache:
                        data = np.concatenate(self._cache)
                        self._cache.clear()
                        
                        self._duration += len(data) / 48000
                        if Config.save_audio and self._file_manager:
                            self._file_manager.write(data)

                        message = AudioMessage(
                            task_id=self.task_id,
                            source='mic',
                            data=base64.b64encode(
                                self._dec.process(data).tobytes()
                            ).decode('utf-8'),
                            is_final=False,
                            time_start=self._start_time,
                            seg_duration=Config.mic_seg_duration,
                            seg_overlap=Config.mic_seg_overlap,
                            context=load_terms() or Config.context,
                            polish=Config.polish,
                            window=getattr(self, '_window', ''),
                            language=Config.language,
                        )
                        asyncio.create_task(self._send_message(message))

                    # 完成写入本地文件
                    if Config.save_audio and self._file_manager:
                        self._file_manager.finish()
                        logger.debug("完成音频文件写入")
                    
                    console.print(f'任务标识：{self.task_id}')
                    console.print(f'    录音时长：{self._duration:.2f}s')
                    logger.info(f"录音任务完成，任务ID: {self.task_id}, 时长: {self._duration:.2f}s")
                    
                    # 告诉服务端音频片段结束了
                    message = AudioMessage(
                        task_id=self.task_id,
                        source='mic',
                        data='',
                        is_final=True,
                        time_start=self._start_time,
                        seg_duration=Config.mic_seg_duration,
                        seg_overlap=Config.mic_seg_overlap,
                        context=load_terms() or Config.context,
                        polish=Config.polish,
                        window=getattr(self, '_window', ''),
                        language=Config.language,
                    )
                    asyncio.create_task(self._send_message(message))
                    break

        except asyncio.CancelledError:
            # 录音被取消（短按时间过短 / 单击模式超时取消）。
            # 此时本会话的 begin 以及阈值前积攒的 data 可能还残留在全局
            # queue_in 中，而该队列由所有录音会话共用。若不清理，下一次
            # record_and_send() 会读到带旧 task_id 语义的脏数据，导致
            # 「前文丢失 / 旧结果混入」的串线问题。
            drained = 0
            while True:
                try:
                    self.state.queue_in.get_nowait()
                    self.state.queue_in.task_done()
                    drained += 1
                except asyncio.QueueEmpty:
                    break
            logger.debug(
                f"录音任务被取消，已排空队列残留 {drained} 条，任务ID: {self.task_id}"
            )
            raise

        except Exception as e:
            logger.error(f"录音任务错误: {e}", exc_info=True)
    
    def get_file_manager(self) -> Optional[AudioFileManager]:
        """获取当前的文件管理器"""
        return self._file_manager
