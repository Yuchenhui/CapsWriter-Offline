"""
自动跟随 Windows 默认录音设备.

PortAudio 只在打开流时取一次默认设备, 之后系统里换了默认麦克风 (手动切 / 插拔无线麦) 它也不知道.
这里每 2 秒用 Core Audio (IMMDeviceEnumerator) 读一次默认录音端点 ID, 变了就在不录音时 reopen().
纯 ctypes 调 COM vtable, 不依赖 comtypes / pycaw.
"""
import ctypes
import logging
import threading
from ctypes import wintypes

logger = logging.getLogger(__name__)

_INTERVAL = 2.0
eCapture, eConsole = 1, 0
CLSCTX_ALL = 0x17


class _GUID(ctypes.Structure):
    _fields_ = [('d1', wintypes.DWORD), ('d2', wintypes.WORD), ('d3', wintypes.WORD), ('d4', ctypes.c_ubyte * 8)]

    def __init__(self, s):
        import uuid
        u = uuid.UUID(s)
        super().__init__(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))


_CLSID_MMDeviceEnumerator = _GUID('BCDE0395-E52F-467C-8E3D-C4579291692E')
_IID_IMMDeviceEnumerator = _GUID('A95664D2-9614-4F35-A746-DE8DB63617E6')


def _vcall(obj, index, restype, *argtypes):
    """取 COM 对象 vtable 第 index 个方法"""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])


def default_capture_id() -> str:
    """当前 Windows 默认录音设备的端点 ID; 失败返回空串. 调用线程需已 CoInitialize."""
    ole32 = ctypes.windll.ole32
    enum = ctypes.c_void_p()
    if ole32.CoCreateInstance(ctypes.byref(_CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                              ctypes.byref(_IID_IMMDeviceEnumerator), ctypes.byref(enum)) != 0:
        return ''
    dev = ctypes.c_void_p()
    try:
        # IMMDeviceEnumerator::GetDefaultAudioEndpoint (vtable 4)
        if _vcall(enum, 4, ctypes.HRESULT, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                enum, eCapture, eConsole, ctypes.byref(dev)):
            return ''
        pid = ctypes.c_wchar_p()
        try:
            # IMMDevice::GetId (vtable 5); 字符串由 CoTaskMemAlloc 分配, 用完要释放
            _vcall(dev, 5, ctypes.HRESULT, ctypes.POINTER(ctypes.c_wchar_p))(dev, ctypes.byref(pid))
            return pid.value or ''
        finally:
            if pid:
                ole32.CoTaskMemFree(pid)
    except OSError:
        return ''
    finally:
        for o in (dev, enum):
            if o:
                _vcall(o, 2, wintypes.ULONG)(o)  # IUnknown::Release


def start(app) -> None:
    def watch():
        ctypes.windll.ole32.CoInitialize(None)
        last = default_capture_id()
        while app.stream._running:
            threading.Event().wait(_INTERVAL)
            cur = default_capture_id()
            if cur and last and cur != last and not app.state.recording:
                logger.info('Windows 默认录音设备已变更, 重开音频流')
                try:
                    app.stream.reopen()
                except Exception as e:
                    logger.warning(f'重开音频流失败: {e}')
            if cur and (cur == last or not app.state.recording):
                last = cur   # 录音中不切, 等录完下一轮再处理

    threading.Thread(target=watch, daemon=True, name='default-mic-watch').start()
