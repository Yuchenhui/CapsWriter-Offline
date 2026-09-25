# coding: utf-8
"""
延迟补标点 (本地改 2026-09-25)

短句末尾标点会被 strip_punc 删掉 (trash_punc_thresh), 连说几句就粘在一起.
这里记住上一句删掉的标点, 下一句若是"接着写"的, 把它补在新句开头:
  - 前台还是同一个窗口
  - 期间没有人工按键 / 鼠标点击 (程序自己注入的不算, 右 Alt / X2 快捷键本身不算)
  - 不超过 _TIMEOUT 秒
touch() 在键盘/鼠标钩子里调用, 只赋一个浮点数, 不做任何慢操作 (钩子卡顿会导致卡键, 见 2026-09-23 事故).
"""
import ctypes
import time

from . import logger

_TIMEOUT = 300

_last_input = 0.0      # 最近一次人工输入 (钩子写)
_memo = None           # (标点, 窗口句柄, 该句开始输出的时刻)
_armed_at = 0.0
_prefix = ''


def _foreground() -> int:
    return ctypes.windll.user32.GetForegroundWindow()


def touch() -> None:
    """钩子: 用户动了键盘或鼠标, 上一句之后不再算"接着写"."""
    global _last_input
    _last_input = time.monotonic()


def arm() -> None:
    """一句话开始输出前调用: 判断能否接上一句, 能则备好前缀, 由第一次写出时取走."""
    global _memo, _armed_at, _prefix
    _armed_at = time.monotonic()
    _prefix = ''
    if _memo:
        punc, hwnd, t = _memo
        why = ('期间有人工输入' if _last_input >= t else '超时' if _armed_at - t >= _TIMEOUT
               else '换了窗口' if _foreground() != hwnd else '')
        if why:
            logger.debug(f'延迟补标点: 不补 {punc!r} ({why})')
        else:
            _prefix = punc + (' ' if punc.isascii() else '')
            logger.info(f'延迟补标点: 接着上一句写, 句首补 {punc!r}')
    _memo = None


def prefixed(text: str) -> str:
    """输出路径第一次写出时调用; 前缀只用一次."""
    global _prefix
    if not _prefix or not text:
        return text
    p, _prefix = _prefix, ''
    return p + text


def remember(punc: str) -> None:
    """一句话输出完后调用: 记下本句被删掉的末尾标点 (没删则清空)."""
    global _memo, _prefix
    _prefix = ''
    _memo = (punc, _foreground(), _armed_at) if punc else None
