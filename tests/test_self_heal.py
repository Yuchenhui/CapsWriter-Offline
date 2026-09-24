"""GPU 丢失自愈 + 客户端重试上限的自检: python tests/test_self_heal.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.server.worker.task_handler import is_gpu_lost
from core.client import server_launcher as sl

# 2026-09-24 实测日志原文
assert is_gpu_lost(RuntimeError('[ONNXRuntimeError] : 1 : FAIL : .../DmlExecutionProvider/src/ExecutionProvider.cpp(952) '
                                '887A0005 The GPU device instance has been suspended.'))
assert is_gpu_lost(RuntimeError('.../DmlExecutionProvider/src/DmlCommandRecorder.cpp(342) 80004005 Unspecified error'))
assert is_gpu_lost(RuntimeError('vk::Queue::submit: ErrorDeviceLost'))
assert not is_gpu_lost(ValueError('audio chunk too short'))

sl._spawn_times.clear()
for t in (0, 10, 20):                              # 3 次放行
    assert sl._may_spawn(t)
    sl._spawn_times.append(t)
assert not sl._may_spawn(30)                       # 第 4 次拒绝
assert sl._may_spawn(0 + sl._SPAWN_WINDOW + 1)     # 窗口滑过后才恢复 (放弃后守护线程已退出, 实际不会再到这)
print('OK')
