"""
录音时给音箱静音 (同 WeType): 防止正在播放的声音被麦克风收进去干扰识别.

按下录音键 -> 记住默认播放设备当前的静音状态, 然后静音; 松开/取消 -> 恢复成原状态
(原本就静音的, 松开后仍静音). 客户端退出时也恢复一次, 防止录音中途退出把音箱留在静音.
纯 ctypes 调 IAudioEndpointVolume, 复用 default_device_watch 的 COM 辅助.
"""
import ctypes
import logging
import threading

from .default_device_watch import _GUID, _vcall, _CLSID_MMDeviceEnumerator, _IID_IMMDeviceEnumerator, CLSCTX_ALL

logger = logging.getLogger(__name__)

_IID_IAudioEndpointVolume = _GUID('5CDF2C82-841E-4546-9722-0CF74078229A')
eRender, eConsole = 0, 0
_lock = threading.Lock()
_saved = None   # None = 当前没有被我们静音; True/False = 静音前的原状态


def _with_volume(fn):
    """拿默认播放设备的 IAudioEndpointVolume 调 fn(vol); 失败返回 None."""
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)   # 各调用线程 (键盘钩子线程等) 各自初始化, 重复调用无害
    enum, dev, vol = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    try:
        if ole32.CoCreateInstance(ctypes.byref(_CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
                                  ctypes.byref(_IID_IMMDeviceEnumerator), ctypes.byref(enum)):
            return None
        # IMMDeviceEnumerator::GetDefaultAudioEndpoint (vtable 4)
        if _vcall(enum, 4, ctypes.HRESULT, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(
                enum, eRender, eConsole, ctypes.byref(dev)):
            return None
        # IMMDevice::Activate (vtable 3)
        if _vcall(dev, 3, ctypes.HRESULT, ctypes.POINTER(_GUID), ctypes.c_uint, ctypes.c_void_p,
                  ctypes.POINTER(ctypes.c_void_p))(dev, ctypes.byref(_IID_IAudioEndpointVolume), CLSCTX_ALL,
                                                   None, ctypes.byref(vol)):
            return None
        return fn(vol)
    except OSError as e:
        logger.debug(f'音箱静音操作失败: {e}')
        return None
    finally:
        for o in (vol, dev, enum):
            if o:
                _vcall(o, 2, ctypes.c_ulong)(o)  # Release


def _get_mute(vol) -> bool:
    m = ctypes.c_int()
    _vcall(vol, 15, ctypes.HRESULT, ctypes.POINTER(ctypes.c_int))(vol, ctypes.byref(m))  # GetMute
    return bool(m.value)


def _set_mute(vol, on: bool):
    _vcall(vol, 14, ctypes.HRESULT, ctypes.c_int, ctypes.c_void_p)(vol, int(on), None)  # SetMute


def mute(still_recording=lambda: True) -> None:
    """still_recording 在锁内再判一次: 松键的 restore() 与延时静音的 Timer 用同一把锁, 避免 restore 先跑成空操作、随后才静音卡住"""
    global _saved
    with _lock:
        if _saved is not None or not still_recording():
            return
        _saved = _with_volume(lambda v: (_get_mute(v), _set_mute(v, True))[0])


def restore() -> None:
    global _saved
    with _lock:
        if _saved is None:
            return
        was = _saved
        _saved = None
        _with_volume(lambda v: _set_mute(v, was))
