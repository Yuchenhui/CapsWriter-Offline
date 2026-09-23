"""
录音设备列表 + 设为 Windows 默认 (托盘「🎤 麦克风」菜单用).

选中后写的是 Windows 默认录音设备 (与声音设置里"设为默认设备"相同, 其他软件也跟着换),
CapsWriter 自己由 default_device_watch 在 2 秒内跟过去 —— 只有一套"当前用哪个"的真源.
纯 ctypes 调 COM vtable, 复用 default_device_watch 的辅助函数.
"""
import ctypes
import logging
from ctypes import wintypes

from .default_device_watch import _GUID, _vcall, _CLSID_MMDeviceEnumerator, _IID_IMMDeviceEnumerator, CLSCTX_ALL

logger = logging.getLogger(__name__)

eCapture, DEVICE_STATE_ACTIVE = 1, 1
_IID_IAudioEndpointVolume = _GUID('5CDF2C82-841E-4546-9722-0CF74078229A')
_CLSID_PolicyConfig = _GUID('870af99c-171d-4f9e-af0d-e63df40c2bc9')
_IID_IPolicyConfig = _GUID('f8679f50-850a-41cf-9c72-430f290290c8')


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [('fmtid', _GUID), ('pid', wintypes.DWORD)]


class _PROPVARIANT(ctypes.Structure):
    _fields_ = [('vt', ctypes.c_ushort), ('r1', ctypes.c_ushort), ('r2', ctypes.c_ushort), ('r3', ctypes.c_ushort),
                ('p', ctypes.c_void_p), ('p2', ctypes.c_void_p)]


_PKEY_FriendlyName = _PROPERTYKEY(_GUID('a45c254e-df1c-4efd-8020-67d146a850e0'), 14)
_P = ctypes.POINTER(ctypes.c_void_p)


def _release(o):
    if o:
        _vcall(o, 2, wintypes.ULONG)(o)


def _com(clsid, iid):
    ctypes.windll.ole32.CoInitialize(None)   # 托盘线程 / 监视线程各自初始化, 重复调用无害
    obj = ctypes.c_void_p()
    if ctypes.windll.ole32.CoCreateInstance(ctypes.byref(clsid), None, CLSCTX_ALL, ctypes.byref(iid), ctypes.byref(obj)):
        return None
    return obj


def _device_info(dev):
    """IMMDevice -> (id, 名称, 是否静音)"""
    ole32 = ctypes.windll.ole32
    pid = ctypes.c_wchar_p()
    _vcall(dev, 5, ctypes.HRESULT, ctypes.POINTER(ctypes.c_wchar_p))(dev, ctypes.byref(pid))   # GetId
    dev_id = pid.value or ''
    ole32.CoTaskMemFree(pid)

    name = dev_id
    store = ctypes.c_void_p()
    if not _vcall(dev, 4, ctypes.HRESULT, wintypes.DWORD, _P)(dev, 0, ctypes.byref(store)):   # OpenPropertyStore(READ)
        pv = _PROPVARIANT()
        if not _vcall(store, 5, ctypes.HRESULT, ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT))(
                store, ctypes.byref(_PKEY_FriendlyName), ctypes.byref(pv)):   # GetValue
            name = ctypes.wstring_at(pv.p) if pv.p else dev_id
            ole32.PropVariantClear(ctypes.byref(pv))
        _release(store)

    muted = False
    vol = ctypes.c_void_p()
    if not _vcall(dev, 3, ctypes.HRESULT, ctypes.POINTER(_GUID), wintypes.DWORD, ctypes.c_void_p, _P)(
            dev, ctypes.byref(_IID_IAudioEndpointVolume), CLSCTX_ALL, None, ctypes.byref(vol)):   # Activate
        m = wintypes.BOOL()
        _vcall(vol, 15, ctypes.HRESULT, ctypes.POINTER(wintypes.BOOL))(vol, ctypes.byref(m))   # GetMute
        muted = bool(m.value)
        _release(vol)
    return dev_id, name, muted


def list_capture() -> list:
    """可用 (ACTIVE) 录音设备: [(id, 名称, 是否静音)]; 失败返回 []"""
    enum = _com(_CLSID_MMDeviceEnumerator, _IID_IMMDeviceEnumerator)
    if not enum:
        return []
    coll = ctypes.c_void_p()
    out = []
    try:
        if _vcall(enum, 3, ctypes.HRESULT, ctypes.c_int, wintypes.DWORD, _P)(
                enum, eCapture, DEVICE_STATE_ACTIVE, ctypes.byref(coll)):   # EnumAudioEndpoints
            return []
        n = wintypes.UINT()
        _vcall(coll, 3, ctypes.HRESULT, ctypes.POINTER(wintypes.UINT))(coll, ctypes.byref(n))   # GetCount
        for i in range(n.value):
            dev = ctypes.c_void_p()
            if _vcall(coll, 4, ctypes.HRESULT, wintypes.UINT, _P)(coll, i, ctypes.byref(dev)):   # Item
                continue
            try:
                out.append(_device_info(dev))
            finally:
                _release(dev)
    except OSError as e:
        logger.debug(f'列录音设备失败: {e}')
    finally:
        _release(coll)
        _release(enum)
    return out


def set_default(dev_id: str) -> bool:
    """设为 Windows 默认录音设备 (控制台 + 多媒体两个角色, 同声音设置里的"设为默认设备")"""
    pc = _com(_CLSID_PolicyConfig, _IID_IPolicyConfig)
    if not pc:
        logger.warning('设默认录音设备失败: 拿不到 IPolicyConfig')
        return False
    try:
        fn = _vcall(pc, 13, ctypes.HRESULT, ctypes.c_wchar_p, ctypes.c_int)   # SetDefaultEndpoint
        for role in (0, 1):
            fn(pc, dev_id, role)
        return True
    except OSError as e:
        logger.warning(f'设默认录音设备失败: {e}')
        return False
    finally:
        _release(pc)


if __name__ == '__main__':   # 只读自检: 列设备 + 当前默认, 不改任何设置
    from .default_device_watch import default_capture_id
    devs = list_capture()   # 内部会 CoInitialize, 先调它
    cur = default_capture_id()
    assert devs, '一个录音设备都没列出来'
    for i, n, m in devs:
        print(('* ' if i == cur else '  ') + n + ('  [静音]' if m else ''))
    assert any(i == cur for i, _, _ in devs), '默认设备不在列表里'
    print('mic_select selftest ok')
