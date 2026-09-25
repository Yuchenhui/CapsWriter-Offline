# coding: utf-8
"""
在线识别 (本地改 2026-09-25): 流式 (CloudStream, 边说边出字) 与非流式 (BatchASR, 松开后整段上传)
两者接口相同: feed() 录音块 -> finish(timeout) 取文字 / cancel(); 由 create() 按托盘「识别」的选择创建, 本地返回 None.

流式 - 千问:

按住快捷键期间把 16 kHz 音频实时推给百炼 qwen-audio-3.1-asr-flash-streaming (DashScope run-task 协议),
中间结果回调给界面显示; 松开后取最终结果交给服务端 (AudioMessage.text), 服务端据此跳过本地识别.
任何失败 (没 key / 连不上 / 报错 / 超时) 都只返回 None, 由本地识别兜底, 不阻塞录音.

实测 (2026-09-25, 同一段 55s 录音): 文字刷新间隔中位 0.92s, 说完后 0.09s 出最后一句; 准确度高于本地 Qwen3-ASR.
qwen3-asr-flash-realtime 固定 2s 刷新一次, 太卡, 不用.
"""
from __future__ import annotations

import asyncio
import base64
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


# 托盘「识别」的选项: key -> (菜单名, 类型, 模型, 需要的 key 环境变量). 价格见 core/tools/asr_usage.PRICES
ENGINES = {
    'qwen-stream': ('千问 qwen-audio-3.1（实时出字）', 'stream', 'qwen-audio-3.1-asr-flash-streaming', 'DASHSCOPE_API_KEY'),
    'qwen-batch': ('千问 qwen3-asr-flash', 'batch', 'qwen3-asr-flash', 'DASHSCOPE_API_KEY'),
    'zhipu-batch': ('智谱 glm-asr-2512', 'batch', 'glm-asr-2512', 'ZHIPU_API_KEY'),
    'mimo-batch': ('小米 mimo-v2.5-asr', 'batch', 'mimo-v2.5-asr', 'MIMO_API_KEY'),
    'minimax-batch': ('MiniMax asr-1.0', 'batch', 'asr-1.0', 'MINIMAX_API_KEY'),
    'local': ('本地（托盘「模型」里选）', 'local', '', ''),
}
_ALIASES = {'cloud': 'qwen-stream'}   # 旧配置值


def engine_key() -> str:
    k = getattr(Config, 'asr_engine', 'local')
    k = _ALIASES.get(k, k)
    return k if k in ENGINES else 'local'


def _has_key(env: str) -> bool:
    pid = {'MINIMAX_API_KEY': 'minimax', 'MIMO_API_KEY': 'mimo', 'ZHIPU_API_KEY': 'zhipu'}.get(env)
    if pid:          # 与二次整理共用 key: MiniMax 另认 mmx-cli 配置; 也读启动后才设的注册表变量
        from core.tools.polish_providers import api_key
        try:
            return bool(api_key(pid))
        except RuntimeError:
            return False
    return bool(os.environ.get(env))


def available() -> bool:
    _, kind, _, env = ENGINES[engine_key()]
    return kind != 'local' and _has_key(env)


def create(on_partial: Callable[[str], None]):
    """按当前选择建一句话的识别实例; 本地 / 没 key 返回 None"""
    if not available():
        return None
    _, kind, model, _ = ENGINES[engine_key()]
    return CloudStream(on_partial, model) if kind == 'stream' else BatchASR(model)


def finish_timeout() -> float:
    """松开后等在线结果的上限: 流式只等收尾 (快); 非流式要上传 + 整段识别 (55s 录音实测 1.7~2.1s)"""
    if ENGINES[engine_key()][1] == 'stream':
        return getattr(Config, 'asr_cloud_timeout', 1.5)
    return getattr(Config, 'asr_batch_timeout', 6.0)


class CloudStream:
    """一句话一个实例: start() -> feed() ... -> finish() / cancel()"""

    def __init__(self, on_partial: Callable[[str], None], model: str = 'qwen-audio-3.1-asr-flash-streaming'):
        self._on_partial, self._model = on_partial, model
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
                asr_usage.add(self._model, self._usage, self._samples / 16000)
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
                            'model': self._model,
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


class BatchASR:
    """非流式: 录音时只攒 16 kHz 样点, 松开后编成 wav 整段上传. 同步 HTTP 放线程里跑, 不堵事件循环"""

    def __init__(self, model: str):
        self._model, self._chunks, self._done = model, [], False

    def feed(self, pcm16k: np.ndarray) -> None:
        if not self._done:
            self._chunks.append((np.clip(pcm16k, -1, 1) * 32767).astype(np.int16))

    def cancel(self) -> None:
        self._done = True

    async def finish(self, timeout: float) -> Optional[str]:
        if self._done or not self._chunks:
            return None
        self._done = True
        pcm = np.concatenate(self._chunks)
        t = time.perf_counter()
        try:
            rec = self._zhipu if self._model == 'glm-asr-2512' else (lambda x: self._recognize(_wav(x)))
            text, billed = await asyncio.wait_for(asyncio.to_thread(rec, pcm), timeout)
        except Exception as e:
            logger.warning(f'在线识别 {self._model} 未在 {timeout}s 内给出结果, 用本地识别 ({type(e).__name__}: {e})')
            return None
        try:
            from core.tools import asr_usage
            asr_usage.add(self._model, {'seconds': billed}, len(pcm) / 16000)
        except Exception as e:
            logger.debug(f'记录在线识别用量失败: {e}')
        if not text.strip():
            logger.warning(f'在线识别 {self._model} {time.perf_counter() - t:.2f}s 后返回空文字, 用本地识别 '
                           f'(音频 {len(pcm) / 16000:.1f}s, 峰值 {int(np.abs(pcm).max()) if len(pcm) else 0})')
            return None
        logger.info(f'在线识别 {self._model} 松开后 {time.perf_counter() - t:.2f}s 出结果 ({len(text)} 字)')
        return text

    def _zhipu(self, pcm: np.ndarray) -> tuple:
        """智谱单次限 30 秒: 超长的在最安静处切段, 各段并行识别再拼接"""
        from concurrent.futures import ThreadPoolExecutor
        parts = _split(pcm)
        if len(parts) == 1:
            return self._recognize(_wav(pcm))
        with ThreadPoolExecutor(len(parts)) as ex:
            res = list(ex.map(lambda x: self._recognize(_wav(x)), parts))
        return ''.join(t for t, _ in res), sum(b for _, b in res)

    def _recognize(self, wav: bytes) -> tuple:
        """-> (文字, 计费秒数)"""
        import urllib.request
        if self._model == 'asr-1.0':            # MiniMax: multipart 上传, 不支持上下文
            from core.tools.polish_providers import api_key
            b = uuid.uuid4().hex
            head = lambda name, extra='': f'--{b}\r\nContent-Disposition: form-data; name="{name}"{extra}\r\n'   # noqa: E731
            body = b''.join([(head('model') + '\r\nasr-1.0\r\n').encode(),
                             (head('response_format') + '\r\njson\r\n').encode(),
                             (head('file', '; filename="a.wav"') + 'Content-Type: audio/wav\r\n\r\n').encode(),
                             wav, f'\r\n--{b}--\r\n'.encode()])
            req = urllib.request.Request('https://api.minimaxi.com/v1/speech_to_text', body,
                                         {'Authorization': 'Bearer ' + api_key('minimax'),
                                          'Content-Type': f'multipart/form-data; boundary={b}'})
            r = json.load(urllib.request.urlopen(req, timeout=30))
            if not r.get('text'):   # 诊断 (2026-09-25 实测连续 3 句返回空, 离线重放同接口却正常): 记下原始返回和送出的音频
                logger.warning(f'MiniMax 返回空文字: {json.dumps(r, ensure_ascii=False)[:300]}')
                try:
                    with open(os.path.join('logs', 'asr_debug_last.wav'), 'wb') as f:
                        f.write(wav)
                except OSError:
                    pass
            return r.get('text', ''), r.get('duration', 0)
        if self._model == 'glm-asr-2512':       # 智谱: GLM Coding Plan 地址 (套餐内), 术语表作热词 (2026-09-26 实测加了明显更准)
            from core.tools.polish_providers import api_key
            from core.tools.terms import load_terms
            b = uuid.uuid4().hex
            words = [w.strip() for w in load_terms().split(',') if w.strip()][:100]   # 官方建议热词不超过 100 个
            fields = [('model', self._model), ('stream', 'false')] + ([('hotwords', json.dumps(words, ensure_ascii=False))] if words else [])
            body = b''.join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields)
            body += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
                     'Content-Type: audio/wav\r\n\r\n').encode() + wav + f'\r\n--{b}--\r\n'.encode()
            req = urllib.request.Request('https://open.bigmodel.cn/api/coding/paas/v4/audio/transcriptions', body,
                                         {'Authorization': 'Bearer ' + api_key('zhipu'),
                                          'Content-Type': f'multipart/form-data; boundary={b}'})
            r = json.load(urllib.request.urlopen(req, timeout=30))
            return r.get('text') or '', (len(wav) - 44) / 32000
        if self._model == 'mimo-v2.5-asr':      # 小米: Token Plan 的 OpenAI 兼容 chat 接口 (同二次整理的 key).
            from core.tools.polish_providers import PROVIDERS, api_key   # 不传术语表: 2026-09-26 实测传了反而更差
            req = urllib.request.Request(PROVIDERS['mimo']['url'], json.dumps({
                'model': self._model, 'asr_options': {'language': 'auto'},
                'messages': [{'role': 'user', 'content': [{'type': 'input_audio', 'input_audio': {
                    'data': 'data:audio/wav;base64,' + base64.b64encode(wav).decode()}}]}]}).encode(),
                {'Authorization': 'Bearer ' + api_key('mimo'), 'Content-Type': 'application/json'})
            r = json.load(urllib.request.urlopen(req, timeout=30))
            return r['choices'][0]['message']['content'] or '', (r.get('usage') or {}).get('seconds', 0)
        from core.tools.terms import load_terms  # 千问 qwen3-asr-flash: OpenAI 兼容, 术语表放 system 作上下文
        messages = [{'role': 'user', 'content': [{'type': 'input_audio', 'input_audio': {
            'data': 'data:audio/wav;base64,' + base64.b64encode(wav).decode()}}]}]
        terms = load_terms()
        if terms:
            messages.insert(0, {'role': 'system', 'content': [{'text': terms}]})
        req = urllib.request.Request('https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
                                     json.dumps({'model': self._model, 'messages': messages, 'stream': False,
                                                 'asr_options': {'enable_itn': True}}).encode(),
                                     {'Authorization': 'Bearer ' + os.environ['DASHSCOPE_API_KEY'],
                                      'Content-Type': 'application/json'})
        r = json.load(urllib.request.urlopen(req, timeout=30))
        return r['choices'][0]['message']['content'] or '', (r.get('usage') or {}).get('seconds', 0)


def _split(pcm: np.ndarray, limit: float = 29.0, lo: float = 20.0) -> list:
    """切成每段 <= limit 秒: 在 [lo, limit] 秒区间里找能量最低的 100ms 处下刀"""
    sr, win, parts = 16000, 1600, []
    while len(pcm) > limit * sr:
        seg = pcm[int(lo * sr):int(limit * sr)].astype(np.float32)
        n = len(seg) // win
        e = (seg[:n * win].reshape(n, win) ** 2).mean(axis=1)
        cut = int(lo * sr) + int(e.argmin()) * win + win // 2
        parts.append(pcm[:cut])
        pcm = pcm[cut:]
    return parts + [pcm]


def _wav(pcm: np.ndarray) -> bytes:
    import io, wave
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm.tobytes())
    return buf.getvalue()


if __name__ == '__main__':   # 自检: python -m core.client.audio.cloud_asr <16k 单声道 wav> [引擎 key]  (按真实语速回放, 真调接口)
    import sys, wave
    logger.addHandler(__import__('logging').StreamHandler()); logger.setLevel('DEBUG')
    Config.asr_engine = sys.argv[2] if len(sys.argv) > 2 else 'cloud'     # 顺带测旧值 cloud 的兼容

    async def main():
        with wave.open(sys.argv[1]) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        n = [0]
        cs = create(lambda t: (n.__setitem__(0, n[0] + 1), print(f'  中间 {len(t):3d} 字: …{t[-24:]}')))
        assert cs is not None, f'{engine_key()} 不可用 (缺 key?)'
        for i in range(0, len(x), 800):                       # 50ms 一块, 与录音回调同节奏
            cs.feed(x[i:i + 800]); await asyncio.sleep(0.05)
        text = await cs.finish(finish_timeout())
        stream = ENGINES[engine_key()][1] == 'stream'
        assert text and (n[0] > 3 if stream else n[0] == 0), (text, n[0])
        print(f'[{engine_key()}] 中间结果 {n[0]} 次')
        print('最终:', text)
        print('cloud_asr selftest ok')
    asyncio.run(main())
