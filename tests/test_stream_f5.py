"""F5 自检: 用桩替换 sounddevice, 不碰真实麦克风. 运行: python tests/test_stream_f5.py"""
import importlib.util
import logging
import sys
import threading
import time
import types
from pathlib import Path

calls = {'terminate': 0, 'initialize': 0, 'active': 0, 'max_active': 0, 'fin_thread': None}


class FakeStream:
    def __init__(self, **kw):
        self.finished = kw['finished_callback']
        self.callback = kw['callback']

    def start(self):
        calls['active'] += 1
        calls['max_active'] = max(calls['max_active'], calls['active'])
        time.sleep(0.05)   # 放大并发窗口
        calls['active'] -= 1

    def close(self):
        self.finished()    # 真实 PortAudio 关流时也会回调 finished


sd = types.ModuleType('sounddevice')
sd.PortAudioError = type('PortAudioError', (Exception,), {})
sd.CallbackFlags = object
sd.InputStream = FakeStream
sd.query_devices = lambda kind=None: {'max_input_channels': 2, 'name': 'fake'}
sd._terminate = lambda: calls.__setitem__('terminate', calls['terminate'] + 1)
sd._initialize = lambda: calls.__setitem__('initialize', calls['initialize'] + 1)
# 故意不提供 sd._ffi / sd._lib: 若代码还在 dlclose, 会走到 AttributeError 分支, 下面断言能发现
sys.modules['sounddevice'] = sd
sys.modules.setdefault('numpy', types.ModuleType('numpy')).ndarray = object
for name in ('core', 'core.client', 'core.client.audio'):
    sys.modules[name] = types.ModuleType(name)
sys.modules['core.client.audio'].logger = logging.getLogger('t')
state_mod = types.ModuleType('core.client.state')
state_mod.console = types.SimpleNamespace(print=lambda *a, **k: None)
sys.modules['core.client.state'] = state_mod

src = Path(__file__).resolve().parents[1] / 'core/client/audio/stream.py'
spec = importlib.util.spec_from_file_location('core.client.audio.stream', src)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

warnings = []
logging.getLogger('t').warning = lambda msg, *a, **k: warnings.append(msg)

app = types.SimpleNamespace(state=types.SimpleNamespace(stream=None, recording=False), loop=None)
m = mod.AudioStreamManager(app)

# 1. 刚开的流不算失效; 超过 1s 没回调算失效; 来一次回调恢复
m.start()
assert m._running and not m.is_stale(1.0)
m._last_cb -= 1.5
assert m.is_stale(1.0)
m._audio_callback(None, 0, None, None)   # recording=False 也要记时间
assert not m.is_stale(1.0)

# 2. 流意外结束: 在"PortAudio 线程"里触发, 重开必须交给别的线程
orig_reopen = m.reopen
m.reopen = lambda: calls.__setitem__('fin_thread', threading.current_thread().name)
m._on_stream_finished()
time.sleep(0.1)
assert calls['fin_thread'] == 'mic-reopen', calls['fin_thread']
m.reopen = orig_reopen

# 3. 重开只 terminate/initialize, 不再卸载共享库
m.reopen()
assert calls['terminate'] == 1 and calls['initialize'] == 1, calls
assert not any('重载 PortAudio' in w for w in warnings), warnings
assert m._running

# 4. 并发重开串行化 (结束回调 / 按键唤醒 / 默认设备跟随 同时触发)
calls['max_active'] = 0
ts = [threading.Thread(target=m.reopen) for _ in range(4)]
[t.start() for t in ts]
[t.join() for t in ts]
assert calls['max_active'] == 1, calls
assert m._running
print('f5 selftest ok')
