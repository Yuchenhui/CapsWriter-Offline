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
import re
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
    'qwen-stream': ('千问 qwen-audio-3.1', 'stream', 'qwen-audio-3.1-asr-flash-streaming', 'DASHSCOPE_API_KEY'),
    'doubao-stream': ('豆包 Seed-ASR 2.0', 'stream', 'doubao-seed-asr-2.0', 'VOLC_ASR_API_KEY'),
    'doubao-batch': ('豆包 Seed-ASR 2.0', 'batch', 'doubao-seed-asr-2.0-nostream', 'VOLC_ASR_API_KEY'),
    'qwen-batch': ('千问 qwen3-asr-flash', 'batch', 'qwen3-asr-flash', 'DASHSCOPE_API_KEY'),
    'zhipu-batch': ('智谱 glm-asr-2512', 'batch', 'glm-asr-2512', 'ZHIPU_API_KEY'),
    'mimo-batch': ('小米 mimo-v2.5-asr', 'batch', 'mimo-v2.5-asr', 'MIMO_API_KEY'),
    'step-batch': ('阶跃 stepaudio-2.5-asr', 'batch', 'stepaudio-2.5-asr', 'STEP_API_KEY'),
    'minimax-batch': ('MiniMax asr-1.0', 'batch', 'asr-1.0', 'MINIMAX_API_KEY'),
    'local': ('本地（托盘「模型」里选）', 'local', '', ''),
}
_ALIASES = {'cloud': 'qwen-stream'}   # 旧配置值


def engine_key() -> str:
    k = getattr(Config, 'asr_engine', 'local')
    k = _ALIASES.get(k, k)
    return k if k in ENGINES else 'local'


def _has_key(env: str) -> bool:
    pid = {'MINIMAX_API_KEY': 'minimax', 'MIMO_API_KEY': 'mimo', 'ZHIPU_API_KEY': 'zhipu', 'KIMI_API_KEY': 'kimi'}.get(env)
    if pid:          # 与二次整理共用 key
        from core.tools.polish_providers import api_key
        try:
            return bool(api_key(pid))
        except RuntimeError:
            return False
    from core.tools.polish_providers import env_key
    return bool(env_key(env))


def available() -> bool:
    _, kind, _, env = ENGINES[engine_key()]
    return kind != 'local' and _has_key(env)


# ---- 失败提示: 云端出问题时在胶囊下方的气泡里提示 (不只写日志). 同一引擎同一类错误 10 分钟内只提示一次 ----
_ALERT_GAP = 600
_alerted: dict = {}
_HINTS = {'balance': ('余额不足', '去控制台充值，或在设置里换引擎'),
          'auth': ('key 无效或服务没开通', '检查环境变量里的 key，或在设置里换引擎'),
          'nokey': ('没设置 key', '在用户环境变量里加上 key，或在设置里换引擎'),
          'rate': ('请求太频繁被限流', '稍后会自动恢复'),
          'timeout': ('响应超时', '网络慢或服务繁忙，下一句会再试'),
          'net': ('连不上服务', '检查网络，下一句会再试'),
          'error': ('接口报错', '详情见 logs/client_latest.log')}


def classify(code, text: str = '') -> str:
    """HTTP 状态码 / 厂商错误码 + 错误文字 -> 错误类别 (_HINTS 的键)"""
    t = f'{code} {text}'.lower()
    if any(k in t for k in ('1113', '余额', 'balance', 'insufficient', 'quota', 'arrearage', '欠费')):
        return 'balance'
    if code in (401, 403) or any(k in t for k in ('not granted', 'invalid api', 'invalidapikey', 'unauthorized', 'access denied', '鉴权')):
        return 'auth'
    if code == 429 or 'too many' in t or 'rate limit' in t:
        return 'rate'
    return 'error'


def classify_exc(e: BaseException) -> tuple:
    """异常 -> (类别, 给日志的详情). HTTPError 读出响应体里的厂商错误码"""
    import urllib.error
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read()[:300].decode('utf-8', 'replace')
        except Exception:
            body = ''
        return classify(e.code, body), f'HTTP {e.code} {body}'
    if isinstance(e, (TimeoutError, asyncio.TimeoutError)):
        return 'timeout', type(e).__name__
    if isinstance(e, (ConnectionError, OSError)):
        return 'net', f'{type(e).__name__}: {e}'
    return 'error', f'{type(e).__name__}: {e}'


_PERSISTENT = {'balance', 'auth', 'nokey'}      # 不会自己好的: 每句都提示 (用户 2026-09-26: 只提示一次就不知道后面都在用本地)


def alert(kind: str) -> None:
    """胶囊下方气泡提示. 10 分钟内首次: 两行 (原因 + 怎么办) 停 5 秒;
    之后: 余额 / key 类每句一行短提示停 2 秒, 超时 / 网络类不再提示 (偶发, 下一句多半就好了)"""
    vendor = ENGINES[engine_key()][0].split()[0]
    now = time.monotonic()
    first = now - _alerted.get((vendor, kind), -_ALERT_GAP) >= _ALERT_GAP
    if not first and kind not in _PERSISTENT:
        return
    if first:
        _alerted[(vendor, kind)] = now
    what, todo = _HINTS.get(kind, _HINTS['error'])
    text, sec = (f'{vendor}：{what}，这句改用了本地识别\n{todo}', 5.0) if first else (f'{vendor}不可用（{what}），这句用了本地识别', 2.0)
    try:
        from core.ui import live_bubble      # 胶囊下方的小气泡 (琥珀色字), 不弹大框
        live_bubble.notice(text, seconds=sec)
    except Exception as e:
        logger.debug(f'弹失败提示失败: {e}')


def end_punc(text: str) -> str:
    """云端结果结尾没有标点时补上 (阶跃 / 智谱 / MiniMax 常不带句末标点; 延迟补标点只补被删掉的, 补不了本来就没有的).
    以 吗 / 呢 结尾补问号, 其余补句号; 末尾已有任何标点或符号的不动"""
    t = text.rstrip()
    if not t or not (t[-1].isalnum() or '一' <= t[-1] <= '鿿'):
        return text
    return t + ('？' if t[-1] in '吗呢' else '。')


def create(on_partial: Callable[[str], None]):
    """按当前选择建一句话的识别实例; 本地 / 没 key 返回 None (没 key 时弹提示)"""
    if not available():
        if ENGINES[engine_key()][1] != 'local':
            alert('nokey')
        return None
    _, kind, model, _ = ENGINES[engine_key()]
    if model.startswith('doubao'):
        return VolcStream(on_partial, model)
    return BatchASR(model) if kind == 'batch' else CloudStream(on_partial, model)


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
            await self._send_finish()
            await asyncio.wait_for(self._finished.wait(), max(0.05, timeout - (time.perf_counter() - t)))
        except Exception as e:
            logger.warning(f'在线识别未在 {timeout}s 内给出结果, 用本地识别 ({type(e).__name__}: {e})')
            if not self._failed:          # 已失败的 (_run 里弹过) 不重复弹
                alert(classify_exc(e)[0])
            self.cancel()
            return None
        ok, text = not self._failed, self._text()
        self.cancel()                         # 收尾关连接 (会置 _failed, 所以先记下 ok)
        if not ok:
            return None
        logger.info(f'在线识别 松开后 {time.perf_counter() - t:.2f}s 出结果 ({len(text)} 字)')
        return end_punc(text)

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
            await self._ws.send(self._pack(data))
        except Exception:
            pass

    def _pack(self, data: bytes) -> bytes:
        return data

    async def _send_finish(self):
        await self._ws.send(json.dumps({'header': {'action': 'finish-task', 'task_id': self._tid, 'streaming': 'duplex'},
                                        'payload': {'input': {}}}))

    async def _run(self):
        from websockets.asyncio.client import connect
        try:
            self._ws = await asyncio.wait_for(connect(URL, additional_headers={
                'Authorization': f'bearer {_dashscope_key()}'}, max_size=2 ** 20), _CONNECT_TIMEOUT)
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
                    code, msg = ev['header'].get('error_code'), ev['header'].get('error_message')
                    logger.warning(f'在线识别失败: {code} {msg}')
                    alert(classify(code, msg or ''))
                    break
        except asyncio.CancelledError:
            return
        except Exception as e:
            kind, detail = classify_exc(e)
            logger.warning(f'在线识别连接失败, 用本地识别: {detail}')
            alert(kind)
        self._failed = True
        self._started.set()      # 唤醒 finish() 的等待, 让它立刻走兜底
        self._finished.set()


class VolcStream(CloudStream):
    """豆包流式语音识别 2.0 (火山引擎 bigmodel_async 二进制协议). 开二遍识别: 边说边出字, 每个分句停顿后用非流式模型重识别.
    2026-09-26 实测同一段 20s 录音: 刷新中位 0.42s, 松开后 0.85s 出最终结果; 准确度不如千问 (PostgreSQL -> Postgres Circle).
    非流式 (model 以 -nostream 结尾): 同一资源的 bigmodel_nostream 接口, 录音时照样边录边传但不出字, 松开后一次给结果, 更准;
    实测同一段 20s 录音松开后 0.96s. 录音文件识别极速版 (volc.bigasr.auc_turbo) 更合适但未开通 (403)."""
    URL = 'wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async'
    URL_NOSTREAM = 'wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream'
    RESOURCE = 'volc.seedasr.sauc.duration'      # 流式 2.0 小时版; 控制台里的实例名不用管
    CHUNK = 3200                                  # 攒够 200ms 再发 (官方: 双向流式 200ms 一包性能最好)

    def __init__(self, on_partial, model='doubao-seed-asr-2.0'):
        self._buf, self._nostream = [], model.endswith('-nostream')
        super().__init__((lambda _t: None) if self._nostream else on_partial, model)

    def feed(self, pcm16k: np.ndarray) -> None:
        self._buf.append(pcm16k)
        if sum(len(x) for x in self._buf) >= self.CHUNK:
            super().feed(np.concatenate(self._buf))
            self._buf = []

    @staticmethod
    def _frame(mtype: int, flags: int, serial: int, payload: bytes) -> bytes:
        import gzip
        body = gzip.compress(payload)
        return bytes([0x11, (mtype << 4) | flags, (serial << 4) | 1, 0]) + len(body).to_bytes(4, 'big') + body

    def _pack(self, data: bytes) -> bytes:
        return self._frame(0b0010, 0, 0, data)

    async def _send_finish(self):
        rest, self._buf = self._buf, []
        tail = (np.clip(np.concatenate(rest), -1, 1) * 32767).astype(np.int16).tobytes() if rest else b''
        self._samples += len(tail) // 2
        await self._ws.send(self._frame(0b0010, 0b0010, 0, tail))       # 最后一包 (可为空)

    async def _run(self):
        import gzip
        from websockets.asyncio.client import connect
        from core.tools.polish_providers import env_key
        from core.tools.terms import load_terms
        try:
            self._ws = await asyncio.wait_for(connect(self.URL_NOSTREAM if self._nostream else self.URL, additional_headers={
                'X-Api-Key': env_key('VOLC_ASR_API_KEY'), 'X-Api-Resource-Id': self.RESOURCE,
                'X-Api-Connect-Id': self._tid}, max_size=2 ** 22), _CONNECT_TIMEOUT)
            req = {'user': {'uid': 'capswriter'}, 'audio': {'format': 'pcm', 'rate': 16000, 'bits': 16, 'channel': 1},
                   'request': {'model_name': 'bigmodel', 'enable_itn': True, 'enable_punc': True,
                               'enable_nonstream': not self._nostream, 'result_type': 'full'}}
            words = [w.strip() for w in load_terms().split(',') if w.strip()][:5000 if self._nostream else 30]  # 热词上限: 双向流式 100 token, nostream 5000 词
            if words:
                req['request']['corpus'] = {'context': json.dumps({'hotwords': [{'word': w} for w in words]}, ensure_ascii=False)}
            await self._ws.send(self._frame(0b0001, 0, 0b0001, json.dumps(req).encode()))
            logger.debug(f'豆包在线识别已连接 {time.perf_counter() - self._t0:.2f}s '
                         f'logid={self._ws.response.headers.get("X-Tt-Logid")}, 补发 {len(self._pending)} 块')
            for d in self._pending:
                await self._ws.send(self._pack(d))
            self._pending.clear()
            self._started.set()
            async for raw in self._ws:
                mtype, flags, comp = raw[1] >> 4, raw[1] & 0x0F, raw[2] & 0x0F
                if mtype == 0b1111:                               # 错误帧: 错误码 + 长度 + 消息
                    msg = raw[12:12 + int.from_bytes(raw[8:12], 'big')]
                    code, text = int.from_bytes(raw[4:8], 'big'), (gzip.decompress(msg) if comp else msg).decode('utf-8', 'replace')[:200]
                    logger.warning(f'豆包在线识别失败: {code} {text}')
                    alert(classify(code, text))
                    break
                p = 8 if flags & 1 else 4                          # 带 sequence 时多 4 字节
                body = raw[p + 4:p + 4 + int.from_bytes(raw[p:p + 4], 'big')]
                res = json.loads((gzip.decompress(body) if comp else body) or b'{}')
                text = (res.get('result') or {}).get('text', '')
                if text and text != self._shown:
                    self._current = self._shown = text
                    try:
                        self._on_partial(text)
                    except Exception as e:
                        logger.debug(f'显示中间结果失败: {e}')
                if flags & 0b0010:                                 # 最后一包的响应 = 最终结果
                    self._current = text or self._current
                    self._finished.set()
                    return
        except asyncio.CancelledError:
            return
        except Exception as e:
            status = getattr(getattr(e, 'response', None), 'status_code', None)   # websockets 握手被拒
            kind = classify(status, str(e)) if status else classify_exc(e)[0]
            logger.warning(f'豆包在线识别连接失败, 用本地识别: {type(e).__name__}: {e}')
            alert(kind)
        self._failed = True
        self._started.set()
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
            kind, detail = classify_exc(e)
            logger.warning(f'在线识别 {self._model} 失败, 用本地识别 ({detail})')
            alert(kind)
            return None
        try:
            from core.tools import asr_usage
            asr_usage.add(self._model, {'seconds': billed}, len(pcm) / 16000)
        except Exception as e:
            logger.debug(f'记录在线识别用量失败: {e}')
        if not re.sub(r'[\W_]+', '', text):      # 空或只有标点 / 符号 = 没识别到
            logger.warning(f'在线识别 {self._model} {time.perf_counter() - t:.2f}s 后返回空文字, 用本地识别 '
                           f'(音频 {len(pcm) / 16000:.1f}s, 峰值 {int(np.abs(pcm).max()) if len(pcm) else 0})')
            return None
        logger.info(f'在线识别 {self._model} 松开后 {time.perf_counter() - t:.2f}s 出结果 ({len(text)} 字)')
        return end_punc(text)

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
        if self._model == 'glm-asr-2512':       # 智谱: 按量计费 0.06 元/分钟, 不在 Coding Plan 内 (走 coding 地址也扣余额); 术语表作热词 (2026-09-26 实测加了明显更准)
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
            text = re.sub(r'\s*#+\s*$', '', r.get('text') or '')     # 几乎没声音时会吐出 "#" (2026-09-26 实测)
            return text, (len(wav) - 44) / 32000
        if self._model.startswith('stepaudio'):  # 阶跃 StepFun: HTTP + SSE, 整段上传, 按量地址 (没订 Step Plan); 术语表作热词
            from core.tools.polish_providers import env_key
            from core.tools.terms import load_terms
            words = [w.strip() for w in load_terms().split(',') if w.strip()]
            body = {'audio': {'data': base64.b64encode(wav).decode(), 'input': {
                'transcription': {'model': self._model, 'language': 'zh', 'enable_itn': True, **({'hotwords': words} if words else {})},
                'format': {'type': 'wav'}}}}
            req = urllib.request.Request('https://api.stepfun.com/v1/audio/asr/sse', json.dumps(body).encode(), {
                'Authorization': 'Bearer ' + env_key('STEP_API_KEY'), 'Content-Type': 'application/json', 'Accept': 'text/event-stream'})
            text = ''
            with urllib.request.urlopen(req, timeout=30) as r:
                for raw in r:
                    line = raw.decode('utf-8', 'replace').strip()
                    if not line.startswith('data:'):
                        continue
                    ev = json.loads(line[5:].strip() or '{}')
                    if ev.get('type') == 'transcript.text.delta':
                        text += ev.get('delta', '')
                    elif ev.get('type') == 'transcript.text.done':
                        text = ev.get('text', text)
                        break
                    elif ev.get('type') == 'error':
                        raise RuntimeError(f"阶跃识别报错: {ev.get('message')}")
            return text, (len(wav) - 44) / 32000
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
                                     {'Authorization': 'Bearer ' + _dashscope_key(),
                                      'Content-Type': 'application/json'})
        r = json.load(urllib.request.urlopen(req, timeout=30))
        return r['choices'][0]['message']['content'] or '', (r.get('usage') or {}).get('seconds', 0)


def _dashscope_key() -> str:
    from core.tools.polish_providers import env_key
    return env_key('DASHSCOPE_API_KEY')


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
