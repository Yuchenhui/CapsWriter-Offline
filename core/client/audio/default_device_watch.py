"""
自动跟随 Windows 默认录音设备.

PortAudio 只在打开流时取一次默认设备, 之后系统里换了默认麦克风 (手动切 / 插拔无线麦) 它也不知道.
这里每 2 秒用 Core Audio (IMMDeviceEnumerator) 读一次默认录音端点 ID, 变了就在不录音时 reopen().
纯 ctypes 调 COM vtable, 不依赖 comtypes / pycaw.
"""
import ctypes
import logging
import threading
import time
from ctypes import wintypes
from typing import Optional

logger = logging.getLogger('client.' + __name__.rsplit('.', 1)[-1])   # 挂到 client 下才会写进 client_latest.log

_INTERVAL = 2.0
_stop = threading.Event()
eCapture, eConsole = 1, 0
CLSCTX_ALL = 0x17

# 本地改 2026-11-08: 探测流的串行收尾队列 (见 _safe_close)
_closer_lock = threading.Lock()
_closer_s: list = []
_closer_thread: Optional[threading.Thread] = None


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


def _alive(name_part: str, timeout: float = 3.0):
    """录 0.5 秒看有没有信号; 找不到设备 / 打不开 / 超时返回 None.
    无线麦发射器关着时接收器仍在线, 只是送全零 (2026-09-26 实测峰值 1e-9); 开着时底噪约 -43 dBFS.
    本地改 2026-11-08: 休眠态的无线麦只枚举不送数据, s.read() 会无限期卡住 (实测 5s+ 零回调),
    卡住时整个 watch 线程停摆且无日志. 改回调模式 + 超时: 到点没数据就当没信号.
    再改 (同日): PortAudio 对休眠设备 stop()/close() 也可能阻塞 (watch 线程实测卡死 5 分钟无日志),
    收尾放守护线程里做, 主线程到点即走"""
    import numpy as np
    import sounddevice as sd
    got = []

    def cb(indata, frames, time_info, status):
        if len(got) < 4:                     # 够判峰值即可, 不白采
            got.append(float(np.abs(indata).max()))

    try:
        # PortAudio 设备表是快照, 只在重开流 / 闲置释放时刷新; 之后才插上的接收器要等到那时才认
        idx = next(i for i, d in enumerate(sd.query_devices()) if d['max_input_channels'] > 0 and name_part in d['name']
                   and sd.query_hostapis(d['hostapi'])['name'] == 'Windows WASAPI')
        rate = int(sd.query_devices(idx)['default_samplerate'])
        s = sd.InputStream(device=idx, channels=1, samplerate=rate, dtype='float32', callback=cb)
        t0 = time.monotonic()
        s.start()
        while time.monotonic() - t0 < timeout and len(got) < 4:
            time.sleep(0.05)
        # 收尾可能卡死: 放守护线程, 不堵 watch (2026-11-08 实测 stop/close 卡 5 分钟)
        threading.Thread(target=_safe_close, args=(s,), daemon=True).start()
    except Exception as e:
        logger.debug(f'探测麦克风 {name_part} 失败: {e}')
        return None
    return bool(got) and max(got) > 1e-6


def _safe_close(s) -> None:
    """收尾关流. 本地改 2026-11-08 再改: 原来每个 _alive 各起一个守护线程关流,
    多个 PortAudio close 在 C 层可能互持锁 (客户端实测: watch 每轮探测 4 个设备 = 4 个并发 close,
    下一轮的开流被堵死, 表现为 watch 跑一轮就停摆且无日志). 改单例线程串行收尾"""
    global _closer_s, _closer_thread
    with _closer_lock:
        _closer_s.append(s)
        if _closer_thread is None or not _closer_thread.is_alive():
            _closer_thread = threading.Thread(target=_closer_loop, daemon=True, name='pa-probe-close')
            _closer_thread.start()


def _closer_loop() -> None:
    while True:
        with _closer_lock:
            s = _closer_s.pop(0) if _closer_s else None
        if s is None:
            return
        try:
            s.stop()
            s.close()
        except Exception:
            pass


def _safe_refresh() -> None:
    """托盘菜单重建 (从 watch 线程异步调; pystray 跨线程调用可能卡, 不拖死设备监听)"""
    try:
        from core.ui.tray import refresh_menu
        refresh_menu()
    except Exception as e:
        logger.debug(f'刷新托盘菜单失败: {e}')


def pick_preferred(priority, devs):
    """按 priority (设备名片段) 顺序, 返回第一个在线、未静音、有信号的端点 ID; 都不行返回 None"""
    for part in priority:
        dev = next((d for d in devs if part in d[1] and not d[2]), None)
        if dev and _alive(part):
            return dev[0]
    return None


def start(app) -> None:
    st = {'sig': None, 'want': None, 'last': None}   # 本地改 2026-11-08: watch/_watch_once 共享状态

    def watch():
        ctypes.windll.ole32.CoInitialize(None)
        st['last'] = default_capture_id()
        from core.client.audio import mic_select
        from config_client import ClientConfig as Config
        while not _stop.is_set():
            # 本地改 2026-11-08: 整轮包 try —— COM 调用 / 探测开流都可能抛, 原来线程一死就静默停摆
            # (自动切换全失效且无日志). 死不了就还能继续跟设备
            try:
                _watch_once(app, Config, mic_select, st)
            except Exception as e:
                logger.warning(f'设备监听轮次出错 (下一轮继续): {e}', exc_info=True)
            threading.Event().wait(_INTERVAL)

    def _watch_once(app, Config, mic_select, st):
        t0 = time.monotonic()   # 本地改 2026-11-08: 分段心跳, 卡死时日志能指出卡在哪一段
        cur = default_capture_id()
        t1 = time.monotonic()
        priority = getattr(Config, 'mic_priority', None)
        switched = False   # 本地改 2026-11-08: 本轮是否主动切过默认设备 (切过就必须 reopen, 见下)
        if priority and getattr(Config, 'mic_auto', True) and app.stream._running and not app.state.recording:   # 麦克风闲置释放时不探测, 否则每 2 秒又把麦克风打开
            with app.stream._lock:   # 闲置释放会重载 PortAudio, 探测开着流时不能被它拆掉
                target = pick_preferred(priority, mic_select.list_capture()) if app.stream._running else None
            t2 = time.monotonic()
            if target and target != cur and target == st['want'] and not app.state.recording:
                logger.info(f'麦克风优先级: 切到 {target}')
                mic_select.set_default(target)
                cur = default_capture_id()
                switched = True   # 本地改 2026-11-08: 原来只改 cur 不改 last, 下一轮 cur==last 判不出变化,
                # 音频流继续录旧设备 (ATK 上电切回后胶囊仍显示 Jabra 的根因). 显式标记, 下面强制 reopen
            st['want'] = target
            logger.debug(f'设备监听轮: 默认端点 {t1-t0:.2f}s / 优先级探测 {t2-t1:.2f}s')
        # 本地改: 插拔 / 静音 / 换默认 -> 托盘「🎤 麦克风」子菜单跟着变
        new_sig = (cur, tuple(mic_select.list_capture()))
        if new_sig != st['sig']:
            if st['sig'] is not None:
                # 本地改 2026-11-08: pystray 的 DestroyMenu/CreatePopupMenu 只能在其消息线程跑,
                # 从 watch 线程直接调会卡死 (实测 watch 停摆无日志). 放守护线程, 卡住也不拖死监听
                from core.ui.tray import refresh_menu
                threading.Thread(target=_safe_refresh, daemon=True).start()
            st['sig'] = new_sig
        if cur and st['last'] and (switched or cur != st['last']) and not app.state.recording and app.stream._running:   # 流已闲置释放时不用 reopen
            logger.info(f'{"优先级切换" if switched else "Windows 默认录音设备已变更"}, 重开音频流')
            try:
                app.stream.reopen()
            except Exception as e:
                logger.warning(f'重开音频流失败: {e}')
        if cur and (cur == st['last'] or not app.state.recording):
            st['last'] = cur   # 录音中不切, 等录完下一轮再处理

    threading.Thread(target=watch, daemon=True, name='default-mic-watch').start()


def stop() -> None:
    _stop.set()
