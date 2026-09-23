"""
按键追踪 (诊断用, 只记录, 不改变任何行为): 排查 2026-09-23 "右 Alt 按下丢失 / Alt 卡住".

写进 client 日志, 行首 [trace], 三路:
  hook   钩子收到的每个 Alt/Ctrl 事件: 消息, flags (INJECTED = 程序模拟的), lag (事件在系统里排了多久),
         cost (我们的过滤函数耗时), 结果 pass/suppress/error (error 会被 pynput 静默吞掉并放行给系统)
  state  独立线程每 20ms 查 GetAsyncKeyState, 记系统眼里 Alt/Ctrl 的按下/松开
  step   切前台窗口 / 恢复静音 等步骤: 走的哪条路, 耗时, 前后 RAlt 状态
判读 —— 钩子没收到 RAlt 按下 (会有 WARN 行) 时看前后的 state 行:
  有 "RAlt ↓"  -> 系统收到了按下, 是我们的钩子被跳过 (回调超时 / 线程卡住)
  没有         -> 按下在到达我们之前就被别的程序的钩子吞了
钩子回调里只做 deque.append, 不做 IO; 写日志在后台线程.
"""
import collections
import ctypes
import threading
import time
from ctypes import wintypes

try:
    from . import logger
except ImportError:   # 单独运行自检时
    import logging as _l
    logger = _l.getLogger('key_trace')

_VKS = {0xA2: 'LCtrl', 0xA3: 'RCtrl', 0xA4: 'LAlt', 0xA5: 'RAlt'}
_MSG = {0x100: 'DOWN', 0x101: 'UP', 0x104: 'SYSDOWN', 0x105: 'SYSUP'}
_UP = (0x101, 0x105)
_u32 = ctypes.WinDLL('user32')
_u32.GetAsyncKeyState.restype = ctypes.c_short
_k32 = ctypes.WinDLL('kernel32')
_k32.GetTickCount.restype = wintypes.DWORD
_q = collections.deque(maxlen=10000)
_last = {}   # vk -> [msg, 重复次数]  (按住不放会自动重复 DOWN, 合并成一行)
_started = False


def _down(vk: int) -> bool:
    return bool(_u32.GetAsyncKeyState(vk) & 0x8000)


def ralt() -> str:
    return '↓' if _down(0xA5) else '↑'


def push(kind: str, text: str) -> None:
    _q.append((time.time(), kind, text))


def hook(msg, data, outcome: str, cost_ms: float) -> None:
    """在钩子回调里调用; 绝不能抛异常 (会覆盖 suppress 的 SuppressException)"""
    try:
        vk = data.vkCode
        if vk not in _VKS:
            return
        prev = _last.get(vk)
        if prev and prev[0] == msg and msg not in _UP:
            prev[1] += 1   # 自动重复的按下
            return
        rep = f' (前一按下重复 {prev[1]} 次)' if prev and prev[1] else ''
        if vk == 0xA5 and msg in _UP and (not prev or prev[0] in _UP):
            push('WARN', '钩子收到 RAlt 松开, 但之前没收到按下 —— 看附近 state 行判断按下去哪了')
        _last[vk] = [msg, 0]
        lag = (_k32.GetTickCount() - data.time) & 0xFFFFFFFF
        inj = ' INJECTED' if data.flags & 0x12 else ''
        push('hook', f'{_VKS[vk]} {_MSG.get(msg, hex(msg))} flags=0x{data.flags:02x}{inj} '
                     f'lag={lag}ms cost={cost_ms:.1f}ms -> {outcome}{rep}')
    except Exception:
        pass


def _poll() -> None:
    last = {vk: _down(vk) for vk in _VKS}
    while True:
        time.sleep(0.02)
        for vk, name in _VKS.items():
            d = _down(vk)
            if d != last[vk]:
                last[vk] = d
                push('state', f"{name} {'↓' if d else '↑'}")


def _writer() -> None:
    while True:
        time.sleep(0.2)
        while _q:
            t, kind, text = _q.popleft()
            ts = time.strftime('%H:%M:%S', time.localtime(t)) + f'.{int(t % 1 * 1000):03d}'
            (logger.warning if kind == 'WARN' else logger.info)(f'[trace] {ts} {kind:5} {text}')


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_poll, daemon=True, name='key-trace-poll').start()
    threading.Thread(target=_writer, daemon=True, name='key-trace-writer').start()
    push('step', f'追踪已启动, 当前 RAlt {ralt()}')


if __name__ == '__main__':   # 自检: 模拟钩子事件, 不碰真实键盘
    class D:
        def __init__(s, vk, fl=0):
            s.vkCode, s.flags, s.time = vk, fl, _k32.GetTickCount()
    hook(0x104, D(0xA5), 'suppress', 0.1)
    hook(0x104, D(0xA5), 'suppress', 0.1)   # 自动重复, 合并
    hook(0x105, D(0xA5), 'suppress', 0.1)
    hook(0x105, D(0xA5), 'pass', 0.1)       # 没见按下的松开 -> WARN
    hook(0x100, D(0x41), 'pass', 0.1)       # 非 Alt/Ctrl 不记
    hook(0x100, D(0xA2, 0x10), 'pass', 0.1)  # 注入的 Ctrl
    out = [k + ' ' + t for _, k, t in _q]
    assert len(out) == 5, out
    assert '重复 1 次' in out[1] and out[2].startswith('WARN') and 'INJECTED' in out[4], out
    print('\n'.join(out))
    print('selftest ok')
