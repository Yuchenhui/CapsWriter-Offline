"""
卡键兜底: 修饰键 (Ctrl/Shift/Alt/Win) 被系统认为"一直按着"时补发松开.

2026-09-23 事故: 钩子吞掉了未见按下的松开, Alt 卡死, 全键盘失灵, 用户无法打字也无法求助.
这里三层兜底: 启动/退出各释放一次; 守护线程每秒检查, 某键连续按着超过 STUCK_SEC 且没在录音就释放并记日志;
托盘菜单「🩹 释放卡住的按键」随时手动触发 (鼠标可点).
补发 KEYUP 对真正按着的键无副作用 (用户松开时系统再收一次 up 而已).
"""
import ctypes
import logging
import threading
import time
from ctypes import wintypes

logger = logging.getLogger(__name__)

STUCK_SEC = 15.0   # Alt+Tab 之类正常长按不会超过这个时间
_MODIFIERS = {0xA0: 'LShift', 0xA1: 'RShift', 0xA2: 'LCtrl', 0xA3: 'RCtrl',
              0xA4: 'LAlt', 0xA5: 'RAlt', 0x5B: 'LWin', 0x5C: 'RWin'}
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
_EXTENDED = {0xA3, 0xA5, 0x5B, 0x5C}   # 右 Ctrl / 右 Alt / Win 是扩展键

_u32 = ctypes.WinDLL('user32')
_u32.GetAsyncKeyState.restype = ctypes.c_short
_u32.MapVirtualKeyW.restype = wintypes.UINT
_stop = threading.Event()


def _is_down(vk: int) -> bool:
    return bool(_u32.GetAsyncKeyState(vk) & 0x8000)


def release(vks=None) -> list:
    """给指定 (默认全部) 修饰键补发 KEYUP, 返回当时系统认为按着的键名."""
    was_down = []
    for vk in (vks or _MODIFIERS):
        if _is_down(vk):
            was_down.append(_MODIFIERS[vk])
        scan = _u32.MapVirtualKeyW(vk, 0)
        flags = KEYEVENTF_KEYUP | (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0)
        _u32.keybd_event(vk, scan, flags, 0)
    if was_down:
        logger.warning(f'补发松开, 之前系统认为按着的键: {was_down}')
    return was_down


def start_watchdog(app) -> None:
    def watch():
        since = {}
        while not _stop.is_set():
            now = time.time()
            for vk, name in _MODIFIERS.items():
                if _is_down(vk):
                    since.setdefault(vk, now)
                    if now - since[vk] > STUCK_SEC and not app.state.recording:
                        logger.warning(f'{name} 连续按着 {now - since[vk]:.0f}s, 判定卡键, 补发松开')
                        release([vk])
                        since.pop(vk, None)
                else:
                    since.pop(vk, None)
            _stop.wait(1.0)
    threading.Thread(target=watch, daemon=True, name='stuck-keys-watchdog').start()


def stop() -> None:
    _stop.set()
