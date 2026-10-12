# coding: utf-8
"""识别引擎连通性测试: probe 的分类 / 结果形状 + 设置页「测试全部」把结果刷进行右侧标签.
不联网 (假引擎 / 假 probe); 界面用真实 Tk 跑一遍."""
import io
import sys
import time
import types
import urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.client.audio import cloud_asr as ca

# ---- 错误分类 (probe 依赖) ----
# 阶跃 Step Plan 额度用尽: 429 + "you have no left credit", 必须判余额而不是限流
assert ca.classify(429, 'you have no left credit for step plan') == 'balance'
assert ca.classify(429, '{"error":{"type":"rate_limited"}}') == 'rate'
assert ca.classify(429, '余额不足或无可用资源包,请充值。') == 'balance'


class FakeInvalidStatus(Exception):
    """像 websockets.InvalidStatus: 带 response.status_code"""
    def __init__(self, code):
        self.response = types.SimpleNamespace(status_code=code)
        super().__init__(f'server rejected WebSocket connection: HTTP {code}')


assert ca.classify_exc(FakeInvalidStatus(401))[0] == 'auth'
assert ca.classify_exc(urllib.error.HTTPError('u', 429, 'x', {}, io.BytesIO(b'no left credit')))[0] == 'balance'
assert ca.classify_exc(ConnectionResetError())[0] == 'net'
assert ca.classify_exc(TimeoutError())[0] == 'timeout'

# ---- probe: 非流式走 BatchASR._recognize ----
ca._has_key = lambda env: True
ca._probe_stream = lambda model, timeout: None            # 流式那支单独测


class FakeBatch:
    plan = {}

    def __init__(self, model, preconnect=True, step_plan=False):
        self.behavior = FakeBatch.plan[(model, step_plan)]

    def _recognize(self, wav):
        b = self.behavior
        if isinstance(b, Exception):
            raise b
        return b


ca.BatchASR = FakeBatch

StepM, QwenM = ca.ENGINES['step-batch'][2], ca.ENGINES['qwen-batch'][2]     # step-batch / step-plan-batch 同 model
FakeBatch.plan = {
    (QwenM, False): ('', 0),                                             # 空文字也算连通
    (ca.ENGINES['zhipu-batch'][2], False): urllib.error.HTTPError('u', 429, 'x', {}, io.BytesIO('余额不足'.encode())),
    (StepM, True): urllib.error.HTTPError('u', 429, 'x', {}, io.BytesIO(b'no left credit for step plan')),
    (StepM, False): ConnectionResetError('boom'),
}
r = ca.probe('qwen-batch')
assert r['ok'] and r['kind'] == '' and r['ms'] >= 0, r
assert ca.probe('zhipu-batch')['kind'] == 'balance'
r = ca.probe('step-plan-batch')
assert (not r['ok']) and r['kind'] == 'balance', r                 # 订阅额度耗尽 = 余额类
assert ca.probe('step-batch')['kind'] == 'net'

# ---- probe: 流式走 _probe_stream, 错误交给 _ProbeFail ----
ca._probe_stream = lambda model, timeout: (_ for _ in ()).throw(ca._ProbeFail('auth', '401 x'))
r = ca.probe('qwen-stream')
assert (not r['ok']) and r['kind'] == 'auth', r
ca._probe_stream = lambda model, timeout: None
assert ca.probe('doubao-stream')['ok']                          # doubao 无论 kind, 都走流式握手

# ---- probe: 缺 key / 本地 ----
ca._has_key = lambda env: False
r = ca.probe('qwen-batch')
assert (not r['ok']) and r['kind'] == 'nokey' and 'DASHSCOPE_API_KEY' in r['detail'], r
assert ca.probe('local')['ok'] is False
print('probe OK')

# ---- 设置页: 「测试全部」把结果刷进各行标签 ----
import tkinter as tk
from core.client.settings.pages import engine as engine_page
from core.client.settings.palette import for_theme


class Ctx:
    def state(self):
        return {'asr_engine': 'mimo-batch', 'asr_fallback': ['qwen-batch'], 'polish': '',
                'polish_structure': False, 'capsule_theme': 'frost', 'vad_enable': False}
    def local_model(self): return 'qwen_asr'
    def local_models(self): return [('qwen_asr', 'qwen_asr', True)]
    def do(self, what, *a): return True


def fake_probe(key, timeout=10.0):
    if key == 'step-plan-batch':
        return {'key': key, 'name': '', 'ok': False, 'kind': 'balance', 'detail': '', 'ms': 12}
    return {'key': key, 'name': '', 'ok': True, 'kind': '', 'detail': '', 'ms': 100}


ca.probe = fake_probe
root = tk.Tk()
page = engine_page.build(root, for_theme('frost'), Ctx())
page.pack(fill='both', expand=True)
root.update_idletasks()


def walk(w):
    yield w
    for c in w.winfo_children():
        yield from walk(c)


rows = [w for w in walk(page) if isinstance(w, tk.Label) and w.cget('text') == '未测试']
assert len(rows) == 9, f'应有 9 个云端引擎行 (不含本地), 实际 {len(rows)}'
btn = next(w for w in walk(page) if isinstance(w, tk.Label) and w.cget('text') == '测试全部')
root.update()                                       # 先映射窗口, 否则 event_generate 的事件会被丢弃
btn.event_generate('<Button-1>', when='now')

end = time.time() + 3.0
while time.time() < end:                            # 等后台线程 + after 轮询把标签刷完
    root.update()
    if all(r.cget('text') not in ('未测试', '测试中…') for r in rows):
        break
    time.sleep(0.02)

texts = [r.cget('text') for r in rows]
assert all(t not in ('未测试', '测试中…') for t in texts), texts
assert sum(t.startswith('✓ 正常') for t in texts) == 8, texts
assert texts.count('✗ 余额不足') == 1, texts
root.destroy()
print('settings page OK')
print('ALL OK')
