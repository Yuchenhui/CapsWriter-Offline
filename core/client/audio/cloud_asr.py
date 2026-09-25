# coding: utf-8
"""
千问在线流式识别 (本地改 2026-09-25)

按住快捷键期间把 16 kHz 音频实时推给百炼 qwen-audio-3.1-asr-flash-streaming (DashScope run-task 协议),
中间结果回调给界面显示; 松开后取最终结果交给服务端 (AudioMessage.text), 服务端据此跳过本地识别.
任何失败 (没 key / 连不上 / 报错 / 超时) 都只返回 None, 由本地识别兜底, 不阻塞录音.

实测 (2026-09-25, 同一段 55s 录音): 文字刷新间隔中位 0.92s, 说完后 0.09s 出最后一句; 准确度高于本地 Qwen3-ASR.
qwen3-asr-flash-realtime 固定 2s 刷新一次, 太卡, 不用.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Callable, Optional

import numpy as np

from config_client import ClientConfig as Config
from . import logger

URL = 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'
_CONNECT_TIMEOUT = 2.0


def available() -> bool:
    return getattr(Config, 'asr_engine', 'local') == 'cloud' and bool(os.environ.get('DASHSCOPE_API_KEY'))


class CloudStream:
    """一句话一个实例: start() -> feed() ... -> finish() / cancel()"""

    def __init__(self, on_partial: Callable[[str], None]):
        self._on_partial = on_partial
        self._ws = None
        self._tid = uuid.uuid4().hex
        self._pending: list[bytes] = []       # 连上之前录到的音频
        self._started = asyncio.Event()
        self._finished = asyncio.Event()
        self._failed = False
        self._sentences: list[str] = []       # 已结束的句子
        self._current = ''                    # 正在说的这句 (会被改写)
        self._shown = ''
        self._usage, self._samples, self._recorded = None, 0, False   # 计费: 最后一次报的累计 usage / 送出的样点数
        self._t0 = time.perf_counter()
        self._tasks = [asyncio.create_task(self._run())]

    # ---- 对外 ----
    def feed(self, pcm16k: np.ndarray) -> None:
        """16 kHz float32 单声道 -> int16 字节, 连上前先攒着"""
        if self._failed:
            return
        data = (np.clip(pcm16k, -1, 1) * 32767).astype(np.int16).tobytes()
        self._samples += len(pcm16k)
        if self._started.is_set() and self._ws is not None:
            self._tasks.append(asyncio.create_task(self._send(data)))
        else:
            self._pending.append(data)

    async def finish(self, timeout: float) -> Optional[str]:
        """松开后调用: 通知结束, 最多等 timeout 秒拿最终结果; 失败 / 超时返回 None"""
        t = time.perf_counter()
        try:
            await asyncio.wait_for(self._started.wait(), timeout)
            if self._failed:
                return None
            await self._ws.send(json.dumps({'header': {'action': 'finish-task', 'task_id': self._tid, 'streaming': 'duplex'},
                                            'payload': {'input': {}}}))
            await asyncio.wait_for(self._finished.wait(), max(0.05, timeout - (time.perf_counter() - t)))
        except Exception as e:
            logger.warning(f'在线识别未在 {timeout}s 内给出结果, 用本地识别 ({type(e).__name__}: {e})')
            self.cancel()
            return None
        ok, text = not self._failed, self._text()
        self.cancel()                         # 收尾关连接 (会置 _failed, 所以先记下 ok)
        if not ok:
            return None
        logger.info(f'在线识别 松开后 {time.perf_counter() - t:.2f}s 出结果 ({len(text)} 字)')
        return text

    def cancel(self) -> None:
        if not self._recorded and self._samples:   # 每句只记一次账 (正常结束 / 超时 / 静音取消都经过这里)
            self._recorded = True
            try:
                from core.tools import asr_usage
                asr_usage.add(getattr(Config, 'asr_cloud_model', ''), self._usage, self._samples / 16000)
            except Exception as e:
                logger.debug(f'记录在线识别用量失败: {e}')
        self._failed = True
        for tk in self._tasks:
            tk.cancel()
        if self._ws is not None:
            asyncio.create_task(self._close())

    # ---- 内部 ----
    def _text(self) -> str:
        return ''.join(self._sentences) + self._current

    async def _close(self):
        try:
            await self._ws.close()
        except Exception:
            pass

    async def _send(self, data: bytes):
        try:
            await self._ws.send(data)
        except Exception:
            pass

    async def _run(self):
        from websockets.asyncio.client import connect
        try:
            self._ws = await asyncio.wait_for(connect(URL, additional_headers={
                'Authorization': f'bearer {os.environ["DASHSCOPE_API_KEY"]}'}, max_size=2 ** 20), _CONNECT_TIMEOUT)
            await self._ws.send(json.dumps({
                'header': {'action': 'run-task', 'task_id': self._tid, 'streaming': 'duplex'},
                'payload': {'task_group': 'audio', 'task': 'asr', 'function': 'recognition',
                            'model': getattr(Config, 'asr_cloud_model', 'qwen-audio-3.1-asr-flash-streaming'),
                            'parameters': {'format': 'pcm', 'sample_rate': 16000}, 'input': {}}}))
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                ev = json.loads(raw)
                name = ev.get('header', {}).get('event')
                if name == 'task-started':
                    logger.debug(f'在线识别已连接 {time.perf_counter() - self._t0:.2f}s, 补发 {len(self._pending)} 块')
                    for d in self._pending:
                        await self._ws.send(d)
                    self._pending.clear()
                    self._started.set()
                elif name == 'result-generated':
                    if ev['payload'].get('usage'):
                        self._usage = ev['payload']['usage']      # 整段累计值, 取最后一次
                    s = ev['payload'].get('output', {}).get('sentence') or {}
                    if s.get('heartbeat'):
                        continue
                    if s.get('sentence_end'):
                        self._sentences.append(s.get('text', '')); self._current = ''
                    else:
                        self._current = s.get('text', '')
                    text = self._text()
                    if text != self._shown:
                        self._shown = text
                        try:
                            self._on_partial(text)
                        except Exception as e:
                            logger.debug(f'显示中间结果失败: {e}')
                elif name == 'task-finished':
                    self._finished.set()
                    return
                elif name == 'task-failed':
                    logger.warning(f'在线识别失败: {ev["header"].get("error_code")} {ev["header"].get("error_message")}')
                    break
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning(f'在线识别连接失败, 用本地识别: {type(e).__name__}: {e}')
        self._failed = True
        self._started.set()      # 唤醒 finish() 的等待, 让它立刻走兜底
        self._finished.set()


if __name__ == '__main__':   # 自检: python -m core.client.audio.cloud_asr <16k 单声道 wav>  (按真实语速回放, 真调接口)
    import sys, wave
    logger.addHandler(__import__('logging').StreamHandler()); logger.setLevel('DEBUG')
    Config.asr_engine = 'cloud'

    async def main():
        with wave.open(sys.argv[1]) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        n = [0]
        cs = CloudStream(lambda t: (n.__setitem__(0, n[0] + 1), print(f'  中间 {len(t):3d} 字: …{t[-24:]}')))
        for i in range(0, len(x), 800):                       # 50ms 一块, 与录音回调同节奏
            cs.feed(x[i:i + 800]); await asyncio.sleep(0.05)
        text = await cs.finish(1.5)
        assert text and n[0] > 3, (text, n[0])
        print('最终:', text)
        print('cloud_asr selftest ok')
    asyncio.run(main())
