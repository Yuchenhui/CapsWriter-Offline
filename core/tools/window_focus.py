"""
按下录音键时激活鼠标下的窗口 (移植自 Vibe Coding.ahk 的 ~RAlt)

用法: 鼠标移到目标窗口上, 按住热键说话, 结果粘贴到那个窗口, 不用先点一下.
CapsWriter 的键盘钩子会吞掉录音键, AHK 那边收不到, 所以这件事只能在这里做.
"""
import ctypes
import logging
import platform
from ctypes import wintypes

logger = logging.getLogger(__name__)

GA_ROOT = 2

if platform.system() == 'Windows':
    # 独立 WinDLL 实例: 下面设的 argtypes/restype 不会污染其他模块用的 ctypes.windll.user32
    user32 = ctypes.WinDLL('user32')
    kernel32 = ctypes.WinDLL('kernel32')
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]


def activate_window_under_cursor() -> None:
    """把鼠标下的顶层窗口切到前台; 已经是前台或拿不到窗口时什么都不做."""
    if platform.system() != 'Windows':
        return
    pt = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return 'no-cursor'
    hwnd = user32.GetAncestor(user32.WindowFromPoint(pt), GA_ROOT)
    fg = user32.GetForegroundWindow()
    if not hwnd or hwnd == fg:
        return 'already-fg'
    if user32.SetForegroundWindow(hwnd):
        return 'direct'
    # 前台锁: 只有"收到最后一次输入"的进程能抢前台. 挂到当前前台线程的输入队列上再试一次.
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    my_tid = kernel32.GetCurrentThreadId()
    attached = bool(fg_tid) and fg_tid != my_tid and user32.AttachThreadInput(my_tid, fg_tid, True)
    ok = False
    try:
        ok = user32.SetForegroundWindow(hwnd)
        if not ok:
            logger.debug(f'激活鼠标下窗口失败 hwnd={hwnd} (提权窗口 / 前台锁)')
    finally:
        if attached:
            user32.AttachThreadInput(my_tid, fg_tid, False)
    return f"attach={'yes' if attached else 'no'} {'ok' if ok else 'fail'}"
