"""原生 TSF 输出客户端自检: 载荷校验 / 桥窗口匹配 / 结果映射, 全部走假 Win32, 不碰真实窗口。
运行: python tests/test_tsf_input.py 或 pytest tests/test_tsf_input.py"""
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock

import importlib.util
from pathlib import Path

_source = Path(__file__).resolve().parents[1] / 'core/tools/tsf_input.py'
_spec = importlib.util.spec_from_file_location('isolated_tsf_input', _source)
ti = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ti)


class FakeWin32:
    """与 ti._Win32 方法面一致的最小替身"""

    def __init__(self, bridges=(), fg=(4321, 100, 200)):
        # bridges: [(hwnd, cls, title, tid, pid)]; fg: (hwnd, tid, pid)
        self.bridges = list(bridges)
        self.fg = fg
        self.sent = []                # [(hwnd, payload, timeout)]
        self.send_result = (True, ti.LRESULT_INSERTED)
        self.winerror = 0

    def foreground_hwnd(self):
        return self.fg[0]

    def window_owner(self, hwnd):
        if hwnd == self.fg[0]:
            return self.fg[1], self.fg[2]
        for h, _cls, _title, tid, pid in self.bridges:
            if h == hwnd:
                return tid, pid
        return 0, 0

    def message_only_windows(self):
        return [h for h, *_ in self.bridges]

    def window_identity(self, hwnd):
        for h, cls, title, *_ in self.bridges:
            if h == hwnd:
                return cls, title
        return '', ''

    def send_copydata(self, hwnd, payload, timeout_ms):
        self.sent.append((hwnd, payload, timeout_ms))
        return self.send_result

    def last_error(self):
        return self.winerror


@contextmanager
def mount(fake):
    """把假 API 挂到 ti._api (send_text 只经 _load_api 取用), 结束后还原"""
    ti._api = fake
    try:
        yield fake
    finally:
        ti._api = None


def bridge(tid=100, pid=200, hwnd=11):
    """前台进程/线程内的合法桥窗口"""
    return (hwnd, ti.BRIDGE_CLASS, 'any-title', tid, pid)


class PayloadTests(unittest.TestCase):
    """冻结协议: 单行 + 无控制字符 + UTF-16 长度上限"""

    def test_valid_single_line(self):
        self.assertIsNone(ti.payload_error('你好世界 hello 123。'))
        self.assertIsNone(ti.payload_error('emoji 😀 ok'))          # 增补平面成对合法
        self.assertIsNone(ti.payload_error('a' * ti.MAX_UTF16_UNITS))

    def test_rejects_c0_del_c1_and_separators(self):
        for bad in ('a\nb', 'a\rb', 'a\tb', 'a\x00b', 'a\x01b', 'a\x1fb', 'a\x7fb',
                    'a\x85b', 'a\u009fb', 'a\u2028b', 'a\u2029b'):
            with self.subTest(bad=repr(bad)):
                self.assertIsNotNone(ti.payload_error(bad))

    def test_rejects_lone_surrogate(self):
        self.assertIsNotNone(ti.payload_error('孤\ud800代理'))

    def test_rejects_non_str_and_empty(self):
        self.assertIsNotNone(ti.payload_error(b'bytes'))
        self.assertIsNotNone(ti.payload_error(''))

    def test_utf16_length_boundary(self):
        self.assertEqual(ti.utf16_units('中'), 1)
        self.assertEqual(ti.utf16_units('\U0001F600'), 2)
        self.assertIsNone(ti.payload_error('\U0001F600' * (ti.MAX_UTF16_UNITS // 2)))
        self.assertIsNotNone(ti.payload_error('\U0001F600' * (ti.MAX_UTF16_UNITS // 2 + 1)))
        self.assertIsNone(ti.payload_error('a' * ti.MAX_UTF16_UNITS))
        self.assertIsNotNone(ti.payload_error('a' * (ti.MAX_UTF16_UNITS + 1)))

    def test_payload_layout_utf16le_with_terminating_nul(self):
        p = ti.copydata_payload('中a')
        self.assertEqual(p, '中a'.encode('utf-16-le') + b'\x00\x00')
        self.assertEqual(len(p), ti.utf16_units('中a') * 2 + 2)   # cbData 含结尾 NUL


class BridgeMatchTests(unittest.TestCase):
    """桥窗口定位: 精确类名 + 前台 PID 且 TID 全等"""

    def test_bridge_in_foreground_pid_and_tid(self):
        with mount(FakeWin32(bridges=[bridge()])) as fake:
            status, _ = ti.send_text('你好')
        self.assertEqual(status, ti.ST_INSERTED)
        self.assertEqual(fake.sent, [(11, ti.copydata_payload('你好'), ti.SEND_TIMEOUT_MS)])

    def test_non_bridge_windows_ignored(self):
        with mount(FakeWin32(bridges=[(5, 'Other.class', 'x', 100, 200), bridge()])) as fake:
            self.assertEqual(ti.send_text('你好')[0], ti.ST_INSERTED)
        self.assertEqual(len(fake.sent), 1)

    def test_title_alias_must_not_match(self):
        """冻结: 只认窗口类 'CapsWriter.TsfBridge.V1', 标题相同但类不对的不算"""
        hwnd, _cls, title, tid, pid = bridge()
        with mount(FakeWin32(bridges=[(hwnd, 'Wrong.class', title, tid, pid)])) as fake:
            status, _ = ti.send_text('你好')
        self.assertEqual(status, ti.ST_NO_BRIDGE)
        self.assertEqual(fake.sent, [])

    def test_bridge_in_other_thread_or_process_rejected(self):
        for tid, pid in ((100, 999), (111, 200)):
            with self.subTest(tid=tid, pid=pid):
                with mount(FakeWin32(bridges=[bridge(tid=tid, pid=pid)])) as fake:
                    status, detail = ti.send_text('你好')
                self.assertEqual(status, ti.ST_NO_BRIDGE)
                self.assertIn('pid=', detail)
                self.assertEqual(fake.sent, [])

    def test_missing_service(self):
        with mount(FakeWin32(bridges=[])):
            status, _ = ti.send_text('你好')
        self.assertEqual(status, ti.ST_NO_BRIDGE)

    def test_no_foreground(self):
        with mount(FakeWin32(bridges=[bridge()], fg=(0, 0, 0))):
            status, _ = ti.send_text('你好')
        self.assertEqual(status, ti.ST_NO_FOREGROUND)

    def test_invalid_payload_never_sent(self):
        with mount(FakeWin32(bridges=[bridge()])) as fake:
            status, detail = ti.send_text('a\nb')
        self.assertEqual(status, ti.ST_INVALID)
        self.assertIn('c0-or-del', detail)
        self.assertEqual(fake.sent, [])


class ResultTests(unittest.TestCase):
    """LRESULT 映射 + 传输失败/超时 = 结果未知, 一次发送无重试"""

    def test_lresult_map(self):
        for code, status in ((1, ti.ST_INSERTED), (2, ti.ST_FOCUS_MISMATCH), (3, ti.ST_NO_CONTEXT),
                             (4, ti.ST_BUSY), (5, ti.ST_INTERNAL), (6, ti.ST_INVALID)):
            self.assertEqual(ti.status_from_lresult(code), (status, ''))

    def test_unexpected_reply_is_unknown(self):
        self.assertEqual(ti.status_from_lresult(99), (ti.ST_UNKNOWN, 'unexpected bridge reply 99'))

    def test_timeout_is_unknown_and_no_retry(self):
        fake = FakeWin32(bridges=[bridge()])
        fake.send_result = (False, 0)
        fake.winerror = 1460                      # ERROR_TIMEOUT
        with mount(fake):
            status, detail = ti.send_text('你好')
        self.assertEqual(status, ti.ST_UNKNOWN)
        self.assertIn('1460', detail)
        self.assertEqual(len(fake.sent), 1)       # 恰好一次, 不重试

    def test_non_windows_is_unavailable_without_api(self):
        real_system, real_api = ti.platform.system, ti._api
        ti._api = None
        ti.platform.system = lambda: 'Linux'
        try:
            status, _ = ti.send_text('你好')
        finally:
            ti.platform.system = real_system
            ti._api = real_api
        self.assertEqual(status, ti.ST_UNAVAILABLE)


class PureGuardTests(unittest.TestCase):
    """非法载荷在加载任何 API 之前就拒绝"""

    def test_invalid_payload_rejected_before_api_load(self):
        with unittest.mock.patch.object(ti, '_load_api', side_effect=AssertionError('API must not be loaded')):
            status, _ = ti.send_text('bad\nline')
        self.assertEqual(status, ti.ST_INVALID)


if __name__ == '__main__':
    unittest.main(verbosity=2)
