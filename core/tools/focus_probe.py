"""
粘贴前探测"前台焦点是不是一个能输入文字的地方" (UI Automation). 目前**只记日志不改行为**:
先收集各应用 (终端 / VS Code / 浏览器地址栏 / 网页空白处 / 桌面) 的真实表现, 再定"没地方输入就留剪贴板并提示"的判定规则.
在独立的 MTA 线程里调, 带超时: 目标程序卡住时 UIA 调用可能阻塞, 不能拖住上屏.
"""
import ctypes
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _Timeout
from ctypes import wintypes

logger = logging.getLogger('client.' + __name__.rsplit('.', 1)[-1])   # 挂到 client 下才会写进 client_latest.log

_CLSID_CUIAutomation = 'ff48dba4-60ef-4201-aa87-54103eef594e'
_IID_IUIAutomation = '30cbe57d-d9d0-452a-ab13-7ac5ac4825ee'
_PROPS = {'ControlType': 30003, 'ClassName': 30012, 'KbFocusable': 30009, 'TextPattern': 30040,
          'ValuePattern': 30043, 'ValueReadOnly': 30046}
_CT = {50004: 'Edit', 50030: 'Document', 50033: 'Pane', 50020: 'Text', 50025: 'Custom', 50032: 'Window',
       50026: 'Group', 50000: 'Button', 50008: 'List', 50007: 'ListItem', 50023: 'Tree', 50024: 'TreeItem'}


class _GUID(ctypes.Structure):
    _fields_ = [('d1', wintypes.DWORD), ('d2', wintypes.WORD), ('d3', wintypes.WORD), ('d4', ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, s):
        import uuid
        u = uuid.UUID(s)
        return cls(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))


class _VARIANT(ctypes.Structure):
    _fields_ = [('vt', ctypes.c_ushort), ('r1', ctypes.c_ushort), ('r2', ctypes.c_ushort), ('r3', ctypes.c_ushort),
                ('val', ctypes.c_longlong), ('val2', ctypes.c_longlong)]


def _vcall(obj, index, restype, *argtypes):
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])


def _init():
    ctypes.windll.ole32.CoInitializeEx(None, 0)   # COINIT_MULTITHREADED: UIA 客户端推荐 MTA


_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='focus-probe', initializer=_init)
_uia = None


def _prop(el, pid):
    v = _VARIANT()
    if _vcall(el, 10, ctypes.HRESULT, ctypes.c_int, ctypes.POINTER(_VARIANT))(el, pid, ctypes.byref(v)):   # GetCurrentPropertyValue
        return None
    try:
        if v.vt == 3:     # VT_I4
            return ctypes.c_int32(v.val & 0xFFFFFFFF).value
        if v.vt == 11:    # VT_BOOL
            return (v.val & 0xFFFF) != 0
        if v.vt == 8:     # VT_BSTR
            return ctypes.wstring_at(ctypes.c_void_p(v.val)) if v.val else ''
        return None
    finally:
        ctypes.windll.oleaut32.VariantClear(ctypes.byref(v))


def _focused(props: dict) -> dict:
    """取前台焦点元素的若干 UIA 属性 (在 _pool 线程里调用)"""
    global _uia
    if _uia is None:
        obj = ctypes.c_void_p()
        hr = ctypes.windll.ole32.CoCreateInstance(ctypes.byref(_GUID.of(_CLSID_CUIAutomation)), None, 1,   # CLSCTX_INPROC_SERVER
                                                  ctypes.byref(_GUID.of(_IID_IUIAutomation)), ctypes.byref(obj))
        if hr:
            return {'error': f'CoCreateInstance 0x{hr & 0xFFFFFFFF:x}'}
        _uia = obj
    el = ctypes.c_void_p()
    if _vcall(_uia, 8, ctypes.HRESULT, ctypes.POINTER(ctypes.c_void_p))(_uia, ctypes.byref(el)) or not el:   # GetFocusedElement
        return {'error': 'no focused element'}
    try:
        return {k: _prop(el, pid) for k, pid in props.items()}
    finally:
        _vcall(el, 2, wintypes.ULONG)(el)   # Release


def _probe() -> dict:
    info = _focused(_PROPS)
    if 'error' in info:
        return info
    ct = info.get('ControlType')
    info['ControlType'] = _CT.get(ct, ct)
    return info


_VALUE_PROPS = {'hwnd': 30020, 'ClassName': 30012, 'Name': 30005, 'ControlType': 30003,
                'HasValue': 30043, 'ReadOnly': 30046, 'Value': 30045}


def focused_value(timeout: float = 0.5) -> dict:
    """自动学习用: 焦点元素身份 (hwnd/类名/名称/类型) + 可编辑时的文本值. 读不到值时 Value 为 None"""
    try:
        r = _pool.submit(_focused, _VALUE_PROPS).result(timeout=timeout)
    except Exception as e:
        return {'error': repr(e)}
    if 'error' not in r and (not r.get('HasValue') or r.get('ReadOnly')):
        r['Value'] = None
    return r


def probe(timeout: float = 0.3) -> dict:
    """前台焦点元素的 UIA 属性; 超时 / 出错返回 {'error': ...}. 从任意线程调用."""
    try:
        return _pool.submit(_probe).result(timeout=timeout)
    except _Timeout:
        return {'error': f'timeout {timeout}s'}
    except Exception as e:
        return {'error': repr(e)}


if __name__ == '__main__':   # 自检: 打印当前前台焦点元素
    import time
    for _ in range(int(__import__('sys').argv[1]) if len(__import__('sys').argv) > 1 else 1):
        r = probe(1.0)
        print(time.strftime('%H:%M:%S'), r, flush=True)
        assert 'error' not in r or r['error'] == 'no focused element', r
        time.sleep(1)
    print('focus_probe selftest ok')
