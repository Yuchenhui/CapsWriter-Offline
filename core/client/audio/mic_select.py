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

    muted, gain = False, None
    vol = _endpoint_volume(dev)
    if vol:
        m = wintypes.BOOL()
        _vcall(vol, 15, ctypes.HRESULT, ctypes.POINTER(wintypes.BOOL))(vol, ctypes.byref(m))   # GetMute
        muted = bool(m.value)
        gain = _get_db(vol)
        _release(vol)
    return dev_id, name, muted, gain


def _endpoint_volume(dev):
    """IMMDevice -> IAudioEndpointVolume (调用方负责 release); 失败 None"""
    vol = ctypes.c_void_p()
    if _vcall(dev, 3, ctypes.HRESULT, ctypes.POINTER(_GUID), wintypes.DWORD, ctypes.c_void_p, _P)(
            dev, ctypes.byref(_IID_IAudioEndpointVolume), CLSCTX_ALL, None, ctypes.byref(vol)):   # Activate
        return None
    return vol


def _get_db(vol):
    db = ctypes.c_float()
    if _vcall(vol, 8, ctypes.HRESULT, ctypes.POINTER(ctypes.c_float))(vol, ctypes.byref(db)):   # GetMasterVolumeLevel
        return None
    return round(db.value, 2)


def _with_device_volume(dev_id, fn):
    """按端点 ID 取 IAudioEndpointVolume 执行 fn(vol); 失败返回 None"""
    enum = _com(_CLSID_MMDeviceEnumerator, _IID_IMMDeviceEnumerator)
    if not enum:
        return None
    dev = ctypes.c_void_p()
    try:
        if _vcall(enum, 5, ctypes.HRESULT, ctypes.c_wchar_p, _P)(enum, dev_id, ctypes.byref(dev)):   # GetDevice
            return None
        vol = _endpoint_volume(dev)
        if not vol:
            return None
        try:
            return fn(vol)
        finally:
            _release(vol)
    except OSError as e:
        logger.warning(f'读写麦克风增益失败: {e}')
        return None
    finally:
        _release(dev)
        _release(enum)


def gain_info(dev_id: str):
    """(当前 dB, 最小 dB, 最大 dB, 步长 dB); 失败 None"""
    def fn(vol):
        mn, mx, inc = ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
        if _vcall(vol, 20, ctypes.HRESULT, *[ctypes.POINTER(ctypes.c_float)] * 3)(
                vol, ctypes.byref(mn), ctypes.byref(mx), ctypes.byref(inc)):   # GetVolumeRange
            return None
        return _get_db(vol), round(mn.value, 2), round(mx.value, 2), round(inc.value, 3)
    return _with_device_volume(dev_id, fn)


def set_gain(dev_id: str, db: float) -> bool:
    """设录音增益 (dB); Windows 按设备保存, 换设备不互相影响"""
    def fn(vol):
        return not _vcall(vol, 6, ctypes.HRESULT, ctypes.c_float, ctypes.c_void_p)(vol, db, None)   # SetMasterVolumeLevel
    return bool(_with_device_volume(dev_id, fn))


def gain_steps(mn: float, mx: float, inc: float, max_items: int = 11) -> list:
    """菜单里给的增益档位: 从最大往下按设备步长取, 档位太多就放宽间隔 (软件音量步长常是 0.03 dB)"""
    inc = inc if inc and inc > 0 else 1.0
    n = int(round((mx - mn) / inc)) + 1
    if n <= max_items:
        return [round(mx - i * inc, 2) for i in range(n)]
    gap = max(1, -(-int(mx - mn) // (max_items - 1)))   # 整数 dB 等间隔, 向上取整
    return [float(v) for v in range(int(mx), int(mn) - 1, -gap) if v >= mn]


def list_capture() -> list:
    """可用 (ACTIVE) 录音设备: [(id, 名称, 是否静音, 增益 dB)]; 失败返回 []"""
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
    for i, n, m, g in devs:
        gi = gain_info(i)
        print(('* ' if i == cur else '  ') + n + ('  [静音]' if m else '') + f'  增益 {g} dB  范围 {gi}')
        assert gi is None or gi[0] == g
        if gi:
            steps = gain_steps(*gi[1:])
            assert 1 < len(steps) <= 11 and steps[0] == gi[2] and steps[-1] >= gi[1], steps
    assert any(i == cur for i, *_ in devs), '默认设备不在列表里'
    assert gain_steps(-15, 5, 2) == [5, 3, 1, -1, -3, -5, -7, -9, -11, -13, -15]
    assert len(gain_steps(-96, 0, 0.03)) <= 11
    print('mic_select selftest ok')
