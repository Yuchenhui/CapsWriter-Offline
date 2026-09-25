# coding: utf-8
"""候补链: 主力成功 / 报错立刻换 / 慢了抢答 / 全失败回本地; 候补顺序过滤 (流式、没 key、走到本地为止). 不联网 (假引擎)"""
import asyncio
import time

import numpy as np

from core.client.audio import cloud_asr as ca

ca.Config.asr_hedge_sec = 0.2
calls = []


class Fake:
    """behavior: (延迟秒, 文字或 None)"""
    plan = {}

    def __init__(self, model, preconnect=True):
        self.model = model
        self.delay, self.text = Fake.plan[model]
        self.cancelled = False

    def feed(self, x):
        pass

    def cancel(self):
        self.cancelled = True

    async def finish(self, timeout):
        calls.append(self.model)
        await asyncio.sleep(self.delay)
        return self.text


ca.BatchASR = Fake
ca._has_key = lambda env: True
pcm = np.zeros(16000, np.float32)


def run(primary_plan, fb_plans, fallbacks):
    calls.clear()
    Fake.plan = dict(fb_plans)
    Fake.plan['primary'] = primary_plan
    ch = ca.Chain('step-batch', Fake('primary'), fallbacks)
    ch.feed(pcm)
    t = time.perf_counter()
    r = asyncio.run(ch.finish(6.0))
    return r, round(time.perf_counter() - t, 2), list(calls)


M = {k: ca.ENGINES[k][2] for k in ('mimo-batch', 'qwen-batch')}
# 1. 主力及时出结果: 不动候补
r, t, c = run((0.05, '主力'), {M['mimo-batch']: (0.05, '小米')}, ['mimo-batch'])
assert r == '主力' and c == ['primary'], (r, c)
# 2. 主力报错 (None): 立刻换候补 1, 不等 hedge
r, t, c = run((0.0, None), {M['mimo-batch']: (0.05, '小米')}, ['mimo-batch'])
assert r == '小米' and t < 0.2, (r, t)
# 3. 主力慢: hedge 0.2s 后同时发给候补 1, 候补先出用候补
r, t, c = run((3.0, '主力'), {M['mimo-batch']: (0.05, '小米')}, ['mimo-batch'])
assert r == '小米' and 0.2 <= t < 0.6, (r, t)
# 4. 主力慢但候补更慢: 主力先回来就用主力
r, t, c = run((0.4, '主力'), {M['mimo-batch']: (3.0, '小米')}, ['mimo-batch'])
assert r == '主力' and t < 0.8, (r, t)
# 5. 都失败: None (回本地), 两个候补都试过
r, t, c = run((0.0, None), {M['mimo-batch']: (0.0, None), M['qwen-batch']: (0.0, None)}, ['mimo-batch', 'qwen-batch'])
assert r is None and c == ['primary', M['mimo-batch'], M['qwen-batch']], (r, c)

# 候补顺序过滤: 去掉主力 / 流式 / 未知; 走到第一个本地为止
ca.Config.asr_engine = 'step-batch'
ca.Config.asr_fallback = ['qwen-stream', 'step-batch', 'mimo-batch', 'nope', 'qwen-batch', 'local:qwen_asr', 'minimax-batch']
assert ca.fallback_keys() == ['mimo-batch', 'qwen-batch'], ca.fallback_keys()
print('OK')
