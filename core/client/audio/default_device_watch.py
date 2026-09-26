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

logger = logging.getLogger('client.' + __name__.rsplit('.', 1)[-1])   # 挂到 client 下才会写进 client_latest.log

_INTERVAL = 2.0
_stop = threading.Event()
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


def _alive(name_part: str):
    """录 0.5 秒看有没有信号; 找不到设备 / 打不开返回 None.
    无线麦发射器关着时接收器仍在线, 只是送全零 (2026-09-26 实测峰值 1e-9); 开着时底噪约 -43 dBFS."""
    import numpy as np
    import sounddevice as sd
    try:
        # ponytail: PortAudio 设备表是启动时的快照, 之后才插上的接收器这里找不到, 等下次重开音频流才认
        idx = next(i for i, d in enumerate(sd.query_devices()) if d['max_input_channels'] > 0 and name_part in d['name']
                   and sd.query_hostapis(d['hostapi'])['name'] == 'Windows WASAPI')
        rate = int(sd.query_devices(idx)['default_samplerate'])
        with sd.InputStream(device=idx, channels=1, samplerate=rate, dtype='float32') as s:
            data, _ = s.read(rate // 2)
        return float(np.abs(data).max()) > 1e-6
    except Exception as e:
        logger.debug(f'探测麦克风 {name_part} 失败: {e}')
        return None


def pick_preferred(priority, devs):
    """按 priority (设备名片段) 顺序, 返回第一个在线、未静音、有信号的端点 ID; 都不行返回 None"""
    for part in priority:
        dev = next((d for d in devs if part in d[1] and not d[2]), None)
        if dev and _alive(part):
            return dev[0]
    return None


def start(app) -> None:
    def watch():
        ctypes.windll.ole32.CoInitialize(None)
        last = default_capture_id()
        from core.client.audio import mic_select
        from config_client import ClientConfig as Config
        sig = None
        want = None   # 优先级选中的设备, 连续两轮一致才切, 防抖
        while not _stop.is_set():
            threading.Event().wait(_INTERVAL)
            cur = default_capture_id()
            priority = getattr(Config, 'mic_priority', None)
            if priority and getattr(Config, 'mic_auto', True) and not app.state.recording:
                target = pick_preferred(priority, mic_select.list_capture())
                if target and target != cur and target == want and not app.state.recording:
                    logger.info(f'麦克风优先级: 切到 {target}')
                    mic_select.set_default(target)
                    cur = default_capture_id()
                want = target
            # 本地改: 插拔 / 静音 / 换默认 -> 托盘「🎤 麦克风」子菜单跟着变
            new_sig = (cur, tuple(mic_select.list_capture()))
            if new_sig != sig:
                if sig is not None:
                    from core.ui.tray import refresh_menu
                    refresh_menu()
                sig = new_sig
            if cur and last and cur != last and not app.state.recording and app.stream._running:   # 流已闲置释放时不用 reopen
                logger.info('Windows 默认录音设备已变更, 重开音频流')
                try:
                    app.stream.reopen()
                except Exception as e:
                    logger.warning(f'重开音频流失败: {e}')
            if cur and (cur == last or not app.state.recording):
                last = cur   # 录音中不切, 等录完下一轮再处理

    threading.Thread(target=watch, daemon=True, name='default-mic-watch').start()


def stop() -> None:
    _stop.set()
