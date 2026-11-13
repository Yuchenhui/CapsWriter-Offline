# coding: utf-8
"""
原生 TSF 输出客户端: 定位前台进程内的 CapsWriter.TsfBridge.V1 桥接窗口, 发送 WM_COPYDATA。

冻结协议 (与原生侧约定, 改动需两侧同步):
- 桥接窗口: message-only 窗口 (HWND_MESSAGE 伪父级下), 窗口类精确为
  'CapsWriter.TsfBridge.V1' (只认这个类, 无标题别名)。客户端只把文本发给 (前台窗口
  PID 与 TID 都等于桥窗口) 的那个; 原生侧收到后还会复核当前前台进程/线程,
  焦点已变化一律拒绝 (返回 2)。
- WM_COPYDATA: COPYDATASTRUCT.dwData = 0x43575453; cbData 含结尾 UTF-16LE NUL。
- 文本: 最长 65535 个 UTF-16 码元 (不含结尾 NUL); 禁止嵌入 NUL、孤立代理、
  C0 (<0x20, 含 CR/LF/TAB)、DEL (0x7F)、C1 (U+0080-U+009F) 与 U+2028/U+2029 行段分隔符
  —— 首版明确只支持单行, 不改写不折叠, 校验不过就整句拒发。
- 桥窗口返回 (SendMessageTimeout 收到的 LRESULT):
  1=已插入; 2=前台/焦点不符; 3=无可写上下文; 4=编辑会话不可用/忙; 5=内部失败; 6=载荷非法。
- 传输失败/超时 = 结果未知 (unknown): 不重试, 不回退剪贴板/模拟按键, 由上层弹提示。

本模块是叶子模块: import 只依赖 logging/platform, 不加载 DLL、不调用任何系统 API;
Win32 在首次使用时惰性加载 (非 Windows 平台一律返回 unavailable)。
"""

from __future__ import annotations

import logging
import platform

logger = logging.getLogger('client.' + __name__.rsplit('.', 1)[-1])

# ---- 冻结协议常量 ----
BRIDGE_CLASS = 'CapsWriter.TsfBridge.V1'   # message-only 桥接窗口的窗口类, 必须精确匹配
COPYDATA_MAGIC = 0x43575453
MAX_UTF16_UNITS = 65535          # 不含结尾 NUL
SEND_TIMEOUT_MS = 1500           # SendMessageTimeout 上限, 约 1.5s

WM_COPYDATA = 0x004A
SMTO_BLOCK = 0x0001              # 拿到结果或超时才返回 (工作线程内阻塞, 不卡事件循环)
SMTO_ABORTIFHUNG = 0x0002

# 桥窗口 LRESULT 语义
LRESULT_INSERTED = 1
LRESULT_FOCUS_MISMATCH = 2
LRESULT_NO_CONTEXT = 3
LRESULT_BUSY = 4
LRESULT_INTERNAL = 5
LRESULT_INVALID = 6

# send_text 返回的 status (上层按此提示, 不回退)
ST_INSERTED = 'inserted'             # 已插入
ST_FOCUS_MISMATCH = 'focus-mismatch' # 确定失败: 前台/焦点不符 (桥复核拒绝)
ST_NO_CONTEXT = 'no-context'         # 确定失败: 无可写上下文
ST_BUSY = 'busy'                     # 确定失败: 编辑会话不可用/忙
ST_INTERNAL = 'internal'             # 确定失败: 桥内部失败
ST_INVALID = 'invalid'               # 本地校验未过 (未发送) 或桥判载荷非法
ST_NO_BRIDGE = 'no-bridge'           # 前台进程/线程里没有桥窗口 (服务未在当前应用加载)
ST_NO_FOREGROUND = 'no-foreground'   # 拿不到前台窗口
ST_UNKNOWN = 'unknown'               # 传输失败/超时, 结果未知 —— 不重试不回退
ST_UNAVAILABLE = 'unavailable'       # 非 Windows / Win32 加载失败

_LRESULT_STATUS = {
    LRESULT_INSERTED: ST_INSERTED,
    LRESULT_FOCUS_MISMATCH: ST_FOCUS_MISMATCH,
    LRESULT_NO_CONTEXT: ST_NO_CONTEXT,
    LRESULT_BUSY: ST_BUSY,
    LRESULT_INTERNAL: ST_INTERNAL,
    LRESULT_INVALID: ST_INVALID,
}


def utf16_units(text: str) -> int:
    """UTF-16 码元数 (增补平面字符算 2)。"""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def payload_error(text: str):
    """校验待发送文本: 合法返回 None, 非法返回原因短标识 (供日志)。
    纯函数, 不调用任何系统 API。冻结协议: 单行 + 无控制字符 + 长度上限。"""
    if not isinstance(text, str):
        return 'not-str'
    if not text:
        return 'empty'
    for ch in text:
        o = ord(ch)
        if o < 0x20 or o == 0x7F:
            return 'c0-or-del'
        if 0x80 <= o <= 0x9F:
            return 'c1-control'
        if o in (0x2028, 0x2029):
            return 'line-separator'
        if 0xD800 <= o <= 0xDFFF:
            return 'lone-surrogate'
    if utf16_units(text) > MAX_UTF16_UNITS:
        return 'too-long'
    return None


def copydata_payload(text: str) -> bytes:
    """合法文本 -> WM_COPYDATA 载荷 (UTF-16LE + 结尾 NUL)。调用方需先过 payload_error。"""
    return text.encode('utf-16-le') + b'\x00\x00'


class _Win32:
    """惰性 Win32 绑定: 独立 WinDLL 实例 + x64 正确签名 (不污染其他模块共享的实例)。"""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._ctypes = ctypes
        self._wintypes = wintypes
        ULONG_PTR = ctypes.c_size_t      # WPARAM / LPARAM(指针实参) / DWORD_PTR
        LONG_PTR = ctypes.c_ssize_t      # LRESULT (LONG_PTR)
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        u = self.user32
        HWND, UINT, DWORD = wintypes.HWND, wintypes.UINT, wintypes.DWORD
        LPCWSTR, LPWSTR, CINT = wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_int
        u.GetForegroundWindow.argtypes = []
        u.GetForegroundWindow.restype = HWND
        u.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(DWORD)]
        u.GetWindowThreadProcessId.restype = DWORD
        u.FindWindowExW.argtypes = [HWND, HWND, LPCWSTR, LPCWSTR]
        u.FindWindowExW.restype = HWND
        u.GetClassNameW.argtypes = [HWND, LPWSTR, CINT]
        u.GetClassNameW.restype = CINT
        u.GetWindowTextW.argtypes = [HWND, LPWSTR, CINT]
        u.GetWindowTextW.restype = CINT
        # LRESULT SendMessageTimeoutW(HWND, UINT, WPARAM, LPARAM, UINT, UINT, PDWORD_PTR)
        u.SendMessageTimeoutW.argtypes = [HWND, UINT, ULONG_PTR, ULONG_PTR, UINT, UINT,
                                          ctypes.POINTER(ULONG_PTR)]
        u.SendMessageTimeoutW.restype = LONG_PTR
        self._HWND_MESSAGE = HWND(-3)    # (HWND)-3: message-only 窗口伪父级

        class COPYDATASTRUCT(ctypes.Structure):
            _fields_ = [('dwData', ULONG_PTR),        # ULONG_PTR
                        ('cbData', wintypes.DWORD),   # 含结尾 UTF-16LE NUL
                        ('lpData', ctypes.c_void_p)]
        self.COPYDATASTRUCT = COPYDATASTRUCT

    def foreground_hwnd(self) -> int:
        return int(self.user32.GetForegroundWindow() or 0)

    def window_owner(self, hwnd) -> tuple:
        """(创建窗口的线程 ID, 进程 ID)"""
        pid = self._wintypes.DWORD(0)
        tid = self.user32.GetWindowThreadProcessId(hwnd, self._ctypes.byref(pid))
        return int(tid), int(pid.value)

    def message_only_windows(self) -> list:
        """HWND_MESSAGE 伪父级下的全部 message-only 顶层窗口 (数量很小)。"""
        out, hwnd = [], self.user32.FindWindowExW(self._HWND_MESSAGE, None, None, None)
        while hwnd:
            out.append(hwnd)
            hwnd = self.user32.FindWindowExW(self._HWND_MESSAGE, hwnd, None, None)
        return out

    def window_identity(self, hwnd) -> tuple:
        """(类名, 窗口标题)"""
        return (self._text(self.user32.GetClassNameW, hwnd),
                self._text(self.user32.GetWindowTextW, hwnd))

    def _text(self, fn, hwnd) -> str:
        buf = self._ctypes.create_unicode_buffer(256)
        fn(hwnd, buf, 256)
        return buf.value

    def send_copydata(self, hwnd, payload: bytes, timeout_ms: int) -> tuple:
        """发一次 WM_COPYDATA, 至多阻塞 timeout_ms。返回 (是否送达, 桥窗口 LRESULT)。"""
        ctypes = self._ctypes
        buf = ctypes.create_string_buffer(payload, len(payload))   # 生命周期覆盖本次调用
        cds = self.COPYDATASTRUCT()
        cds.dwData = COPYDATA_MAGIC
        cds.cbData = len(payload)                                  # 含结尾 UTF-16LE NUL
        cds.lpData = ctypes.cast(buf, ctypes.c_void_p)
        result = ctypes.c_size_t(0)
        sent = self.user32.SendMessageTimeoutW(hwnd, WM_COPYDATA, 0, ctypes.addressof(cds),
                                               SMTO_BLOCK | SMTO_ABORTIFHUNG, timeout_ms,
                                               ctypes.byref(result))
        if not sent:
            return False, 0
        return True, int(result.value)

    def last_error(self) -> int:
        return self._ctypes.get_last_error()


_api = None    # 惰性缓存的 _Win32; False = 非 Windows / 加载失败


def _load_api():
    """首次使用才加载 user32; 非 Windows 平台返回 None (send/find 全部短路)。"""
    global _api
    if _api is None:
        if platform.system() != 'Windows':
            _api = False
        else:
            try:
                _api = _Win32()
            except Exception as e:
                logger.warning(f'加载 Win32 API 失败: {e}')
                _api = False
    return _api or None


def _is_bridge_window(cls: str) -> bool:
    """窗口类名精确命中桥接窗口 (冻结: 只认 'CapsWriter.TsfBridge.V1', 无标题别名)。"""
    return cls == BRIDGE_CLASS


def _bridge_for_foreground(api) -> tuple:
    """返回 (桥窗口句柄或 0, 原因字符串)。只接受窗口类精确为 BRIDGE_CLASS,
    且 PID 与 TID 都等于当前前台窗口的桥窗口。"""
    fg = api.foreground_hwnd()
    if not fg:
        return 0, 'no foreground window'
    fg_tid, fg_pid = api.window_owner(fg)
    mismatch = ''
    for hwnd in api.message_only_windows():
        cls, _title = api.window_identity(hwnd)
        if not _is_bridge_window(cls):
            continue
        tid, pid = api.window_owner(hwnd)
        if pid == fg_pid and tid == fg_tid:
            return hwnd, ''
        mismatch = f'bridge pid={pid} tid={tid} != foreground pid={fg_pid} tid={fg_tid}'
    if mismatch:
        return 0, mismatch
    return 0, 'no bridge window found (CapsWriter Voice service not loaded in foreground app?)'


def status_from_lresult(code: int) -> tuple:
    """桥窗口 LRESULT -> (status, detail); 未约定的返回值视为结果未知。"""
    status = _LRESULT_STATUS.get(code)
    if status:
        return status, ''
    return ST_UNKNOWN, f'unexpected bridge reply {code}'


def send_text(text: str, timeout_ms: int = SEND_TIMEOUT_MS) -> tuple:
    """把一句单行文本经 WM_COPYDATA 交给前台进程内的 TSF 桥接窗口。

    返回 (status, detail)。status=ST_INSERTED 表示桥窗口已在 TSF 编辑会话里插入文本;
    ST_UNKNOWN 表示传输失败/超时, 结果未知 —— 调用方不得重试或回退。
    本函数同步阻塞 (至多 timeout_ms); 事件循环请走 native_tsf.output_native。
    """
    err = payload_error(text)
    if err:
        return ST_INVALID, f'payload rejected locally: {err}'
    api = _load_api()
    if api is None:
        return ST_UNAVAILABLE, 'Win32 APIs unavailable (non-Windows platform?)'
    hwnd, why = _bridge_for_foreground(api)
    if not hwnd:
        if why == 'no foreground window':
            return ST_NO_FOREGROUND, why
        return ST_NO_BRIDGE, why
    payload = copydata_payload(text)
    sent, reply = api.send_copydata(hwnd, payload, timeout_ms)
    if not sent:
        return ST_UNKNOWN, f'SendMessageTimeout failed (winerror={api.last_error()})'
    return status_from_lresult(reply)


# ---- 自动临时切换输入法: 输出前切到 CapsWriter Voice, 发完自动还原用户的输入法 ----

OUR_PROFILE = '{B96C3F71-DB8A-4FBA-9F27-8F2D7F819D7A}|{A41CCB7E-34BB-4206-A4A7-F18100418359}|2052'
BRIDGE_WAIT_SEC = 0.8     # 切换后等待前台应用创建桥窗口的上限
BRIDGE_POLL_SEC = 0.05
CAPTURE_CCH = 128

_dll_cache = None         # 加载成功的 ctypes DLL; False = 加载失败 (不重试)


def _profile_api(dll_path: str):
    """惰性加载原生组件的 TsfProfileCapture/TsfProfileSwitch 导出; 失败返回 None。"""
    global _dll_cache
    if _dll_cache is not None:
        return _dll_cache if _dll_cache is not False else None
    if platform.system() != 'Windows':
        _dll_cache = False
        return None
    try:
        import ctypes
        import os
        dll = ctypes.WinDLL(os.path.abspath(dll_path), use_last_error=True)
        dll.TsfProfileCapture.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
        dll.TsfProfileCapture.restype = ctypes.c_uint          # 写入的字符数, 0=失败
        dll.TsfProfileSwitch.argtypes = [ctypes.c_wchar_p]
        dll.TsfProfileSwitch.restype = ctypes.c_long           # HRESULT
        _dll_cache = dll
        return dll
    except Exception as e:
        logger.warning(f'加载 TSF 组件失败: {dll_path} -> {e!r}')
        _dll_cache = False
        return None


def send_text_auto(text: str, dll_path: str, timeout_ms: int = SEND_TIMEOUT_MS) -> tuple:
    """send_text 的自动切换版: 前台没有桥窗口时, 临时切到 CapsWriter Voice,
    等桥窗口出现后发送, finally 里无条件还原切换前的输入法。

    - 前台已有桥窗口 (用户手动选了输入法) -> 直接 send_text, 不做任何切换。
    - 读不到当前输入法 (纯键盘布局等) -> 拒发, 避免切过去还原不回来。
    - 切换失败 / 桥窗口未出现 -> 拒发并如实报告; 只有切换成功过才需要还原。
    - 发送失败/超时 (unknown) 也照常还原, 结果语义与 send_text 一致。
    """
    err = payload_error(text)
    if err:
        return ST_INVALID, f'payload rejected locally: {err}'
    api = _load_api()
    if api is None:
        return ST_UNAVAILABLE, 'Win32 APIs unavailable (non-Windows platform?)'
    hwnd, _why = _bridge_for_foreground(api)
    if hwnd:
        return send_text(text, timeout_ms)
    prof = _profile_api(dll_path)
    if prof is None:
        return ST_NO_BRIDGE, f'TSF profile component unavailable: {dll_path}'
    import time as _time
    captured = _capture_profile(prof)
    if not captured:
        return ST_NO_BRIDGE, '无法读取当前输入法 (GetActiveProfile), 不做切换'
    if captured != OUR_PROFILE:
        hr = prof.TsfProfileSwitch(OUR_PROFILE)
        if hr != 0:
            return ST_NO_BRIDGE, f'切换输入法失败 HRESULT=0x{hr & 0xFFFFFFFF:08X}'
    deadline = _time.monotonic() + BRIDGE_WAIT_SEC
    while True:
        hwnd, why = _bridge_for_foreground(api)
        if hwnd:
            break
        if _time.monotonic() >= deadline:
            hwnd, why = 0, '切换后前台未出现桥窗口'
            break
        _time.sleep(BRIDGE_POLL_SEC)
    try:
        if hwnd:
            return send_text(text, timeout_ms)
        return ST_NO_BRIDGE, why
    finally:
        if captured != OUR_PROFILE:
            hr = prof.TsfProfileSwitch(captured)
            if hr != 0:
                logger.warning(f'还原输入法失败 HRESULT=0x{hr & 0xFFFFFFFF:08X}, 当前停在: {captured}')


def _capture_profile(prof) -> str:
    """读取当前激活的键盘类文本服务, 返回 'clsid|guid|langid'; 失败/纯键盘布局返回 ''。"""
    import ctypes
    buf = ctypes.create_unicode_buffer(CAPTURE_CCH)
    written = prof.TsfProfileCapture(buf, CAPTURE_CCH)
    if not written or written >= CAPTURE_CCH:
        return ''
    return buf.value
