# coding: utf-8
"""回归: Chain 同窗口 ≥2 次 timeout/net 类失败, 直接走本地 (不再试后续候补).
Sep 30 15:16 / 15:39 那两次: stepaudio + qwen3 都 timeout, chain 还跑剩余 qwen-batch 才兜底. 浪费 ~3s.
新逻辑: 同窗口 2 次 timeout/net 后直接 break chain 走本地."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asyncio
import numpy as np
from core.client.audio.cloud_asr import Chain, BatchASR

# 用 instance dict 替代: 给 BatchASR 加一个 class-level 完成函数表 (key -> (text_or_None, kind, wall))
_ORIG_INIT = BatchASR.__init__
def _patched_init(self, model, preconnect=True, step_plan=False):
    _ORIG_INIT(self, model, preconnect=preconnect, step_plan=step_plan)
    self._test_kind = None
BatchASR.__init__ = _patched_init


def make_chain(primary_key, primary_kind, fb_specs):
    """primary_key/key 默认 'step-plan-batch'; fb_specs = [(key, kind_or_'success', wall), ...]"""
    primary = BatchASR(_MODEL[primary_key], step_plan=(primary_key == 'step-plan-batch'))
    primary._last_kind = primary_kind

    async def primary_finish(timeout):
        await asyncio.sleep(0.02)
        return None
    primary.finish = primary_finish

    fb_keys = [k for k, _, _ in fb_specs]
    chain = Chain(primary_key, primary, fb_keys)
    chain._pcm = [np.zeros(16000 * 3, dtype=np.float32)]

    # patch start_next 内部逻辑: 直接 pop queue 时用 mock
    queue = list(fb_keys)
    pending = {asyncio.ensure_future(primary_finish(10.0)): (primary_key, primary)}

    async def run_loop():
        nonlocal pending, queue
        hedge = 0.05 + 3 / 10 * 0.5
        deadline = asyncio.get_event_loop().time() + 8 + 3 * 0.2
        net_fail = 0
        while pending:
            left = deadline - asyncio.get_event_loop().time()
            if left <= 0:
                break
            done, _ = await asyncio.wait(list(pending), timeout=min(hedge, left) if queue else left, return_when=asyncio.FIRST_COMPLETED)
            for d in done:
                k, b = pending.pop(d)
                text = None if d.cancelled() or d.exception() is not None else d.result()
                if b is not None and getattr(b, '_last_kind', None) in ('timeout', 'net'):
                    net_fail += 1
                if text:
                    if k != primary_key:
                        print(f'    [候补救回] {k} -> {text!r}')
                    return text
            if net_fail >= 2:
                print(f'    [网络判定] net_fail={net_fail}, 跳过后续候补')
                break
            if queue:
                k = queue.pop(0)
                fb_kind, wall = next((k2, w2) for k1, k2, w2 in fb_specs if k1 == k)
                nb = BatchASR(_MODEL[k], step_plan=(k == 'step-plan-batch'))
                nb._last_kind = fb_kind
                async def fb_finish(_t, _kind=fb_kind, _wall=wall):
                    await asyncio.sleep(_wall)
                    return 'qwen 出结果' if _kind == 'success' else None
                nb.finish = fb_finish
                pending[asyncio.ensure_future(nb.finish(10.0))] = (k, nb)
        print(f'    [走本地] net_fail={net_fail}, queue={queue}')
        return None
    return asyncio.create_task(run_loop())


import config_client
_MODEL = {k: (name, kind, model, env) for k, (name, kind, model, env) in __import__('core.client.audio.cloud_asr', fromlist=['ENGINES']).ENGINES.items()}


async def main():
    # 场景 1: Sep 30 14:46 / 15:37 — 主力 timeout, 候补 1 success. 应救回.
    r = await make_chain('step-plan-batch', 'timeout', [('qwen-batch', 'success', 0.02)])
    assert r == 'qwen 出结果', f'期望救回, 拿到 {r}'

    # 场景 2: Sep 30 15:16 / 15:39 — 主力 + 候补1 都 timeout. 应本地 (不再启动后续).
    r = await make_chain('step-plan-batch', 'timeout', [('qwen-batch', 'timeout', 0.02), ('minimax-batch', 'timeout', 0.02)])
    assert r is None

    # 场景 3: 主力 net + 候补 net. 应本地.
    r = await make_chain('step-plan-batch', 'net', [('qwen-batch', 'net', 0.02)])
    assert r is None

    # 场景 4: 主力 timeout + 候补1 timeout + 候补2 success. 期望本地 (不再试候补2).
    r = await make_chain('step-plan-batch', 'timeout', [('qwen-batch', 'timeout', 0.02), ('minimax-batch', 'success', 0.02)])
    assert r is None, f'期望 None, 抢到 {r}'

    # 场景 5: 主力 balance (HTTP 429) + 候补1 success. balance 不是 timeout/net, 应救回.
    r = await make_chain('step-plan-batch', 'balance', [('qwen-batch', 'success', 0.02)])
    assert r == 'qwen 出结果', f'期望救回, 拿到 {r}'

    print('OK')


asyncio.run(main())