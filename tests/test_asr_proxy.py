"""本地识别托管代理自检: python tests/test_asr_proxy.py"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.server.engines import manager

made = []
class Fake:
    capabilities = ('ASR',)
    def __init__(self): self.alive = True; made.append(self)
    def create_stream(self): return 'stream'
    def cleanup(self): self.alive = False

clock = [1000.0]
manager.time.time = lambda: clock[0]
p = manager.ManagedASRProxy(Fake, timeout_sec=60)
assert len(made) == 1 and p.capabilities == ('ASR',)

p.mark(online=False); clock[0] += 3600; p.check_idle()
assert p.engine is not None, '本地识别时永不卸载'

p.mark(online=True); clock[0] += 30; p.check_idle()
assert p.engine is not None, '闲置未满 60s 不卸载'
clock[0] += 31; p.check_idle()
assert p.engine is None and not made[0].alive, '在线 + 闲置满 60s -> 卸载'

assert p.create_stream() == 'stream' and len(made) == 2, '卸载后访问自动重载'
p.mark(online=False); clock[0] += 3600; p.check_idle()
assert p.engine is made[1], '兜底 (本地) 之后不卸载'
print('OK')
