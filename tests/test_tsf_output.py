"""TSF 路由与持久化验证，不启动客户端、音频、输入钩子或真实 Win32。
运行: python -B -m unittest discover -s tests -p 'test_tsf_*.py'
"""
import asyncio
import importlib.util
import json
import logging
from pathlib import Path
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

_ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def isolated_client_modules():
    """加载真实叶子模块，仅替换平台 I/O 和重量级包入口，退出后还原模块表。"""
    replacements = {}
    log = logging.getLogger('isolated.tsf.tests')
    log.addHandler(logging.NullHandler())
    log.propagate = False
    for name in ('core', 'core.tools', 'core.client', 'core.client.output',
                 'core.client.llm', 'core.client.ui', 'core.client.settings'):
        module = types.ModuleType(name)
        module.__path__ = [str(_ROOT.joinpath(*name.split('.')))]
        module.logger = log
        replacements[name] = module

    def leaf(name, **attributes):
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        replacements[name] = module
        return module

    async def forbidden_paste(*args, **kwargs):
        raise AssertionError('unmocked clipboard access')

    def forbidden_keyboard(*args, **kwargs):
        raise AssertionError('unmocked keyboard access')

    leaf('keyboard', write=forbidden_keyboard)
    leaf('core.client.clipboard', paste_text=forbidden_paste)
    leaf('core.client.ui.recording_toast', close_active=lambda: None,
         hint_capsule=lambda *a, **k: None)
    leaf('core.tools.window_detector', get_active_window_info=lambda: {'process_name': ''})
    leaf('core.tools.polish_providers', resolve=lambda value: value)
    leaf('core.client.llm.llm_error_handler',
         handle_llm_error=lambda error, content, role: (content, False))
    for name, module in replacements.items():
        parent, _, child = name.rpartition('.')
        if parent in replacements:
            setattr(replacements[parent], child, module)

    with patch.dict(sys.modules, replacements):
        def load(name, relative):
            spec = importlib.util.spec_from_file_location(name, _ROOT / relative)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            parent, _, child = name.rpartition('.')
            if parent:
                setattr(sys.modules[parent], child, module)
            return module

        config = load('config_client', 'config_client.py')
        load('core.tools.asyncio_to_thread', 'core/tools/asyncio_to_thread.py')
        load('core.tools.tsf_input', 'core/tools/tsf_input.py')
        load('core.client.output.carry_punc', 'core/client/output/carry_punc.py')
        load('core.client.output.native_tsf', 'core/client/output/native_tsf.py')
        load('core.client.output.text_output', 'core/client/output/text_output.py')
        load('core.client.llm.llm_output_typing', 'core/client/llm/llm_output_typing.py')
        load('core.client.user_state', 'core/client/user_state.py')
        load('core.client.settings.actions', 'core/client/settings/actions.py')
        load('core.client.settings.context', 'core/client/settings/context.py')
        yield types.SimpleNamespace(
            config=config, keyboard=sys.modules['keyboard'],
            cp=sys.modules['core.client.output.carry_punc'],
            native=sys.modules['core.client.output.native_tsf'],
            output=sys.modules['core.client.output.text_output'],
            llm=sys.modules['core.client.llm.llm_output_typing'],
            input=sys.modules['core.tools.tsf_input'],
            state=sys.modules['core.client.user_state'],
            context=sys.modules['core.client.settings.context'])


class FakeSend:
    """替换 core.tools.tsf_input.send_text 的假发送 (记录调用, 返回固定结果)"""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, text, timeout_ms=1500):
        self.calls.append(text)
        return self.result


class _FakeRole:
    name = 'Role'


class _FakeMonitor:
    def reset(self):
        pass

    def should_stop(self):
        return False


class _FakeHandler:
    """llm_output_typing 所需的 handler 面: monitor / detect_role / process"""

    def __init__(self, result=('润色结果', 5, 1.0)):
        self.monitor = _FakeMonitor()
        self.result = result
        self.got_stream_cb = 'not-called'

    def detect_role(self, text):
        return _FakeRole(), text

    def process(self, role_config, content, matched_hotwords, stream_cb):
        self.got_stream_cb = stream_cb
        return self.result


class RouteTestBase(unittest.TestCase):
    """公共夹具: 模式保存/还原, 剪贴板与键盘全部 mock, 关掉会碰真实前台窗口的配置"""

    def setUp(self):
        self.modules_context = isolated_client_modules()
        self.modules = self.modules_context.__enter__()
        self.addCleanup(self.modules_context.__exit__, None, None, None)
        global keyboard, ClientConfig, cp, native_tsf, TextOutput, lot, tsf_input
        keyboard = self.modules.keyboard
        ClientConfig = self.modules.config.ClientConfig
        cp, native_tsf = self.modules.cp, self.modules.native
        TextOutput = self.modules.output.TextOutput
        lot, tsf_input = self.modules.llm, self.modules.input
        self._old = (getattr(ClientConfig, 'output_mode', 'clipboard'),
                     ClientConfig.trash_punc_apps,
                     cp._memo, cp._prefix, cp._last_input)
        ClientConfig.trash_punc_apps = []            # strip_punc 不去探测真实前台窗口
        cp._memo, cp._prefix, cp._last_input = None, '', 0.0
        self.mock_paste = AsyncMock()
        self.mock_write = MagicMock()
        patchers = [
            patch('core.client.llm.llm_output_typing.paste_text', self.mock_paste),  # lot 顶层 import
            patch('core.client.clipboard.paste_text', self.mock_paste),              # text_output 懒 import
            patch.object(keyboard, 'write', self.mock_write),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._restore)

    def _restore(self):
        (mode, apps, memo, prefix, last_input) = self._old
        ClientConfig.output_mode = mode
        ClientConfig.trash_punc_apps = apps
        cp._memo, cp._prefix, cp._last_input = memo, prefix, last_input


class DisabledModeTests(RouteTestBase):
    """mode=clipboard (默认/关闭): 行为与以前一致, 绝不触达原生路径"""

    def test_clipboard_mode_pastes_and_never_touches_native(self):
        for mode in ('clipboard', None, '', 'tsfx'):
            with self.subTest(mode=mode):
                ClientConfig.output_mode = mode
                dead = MagicMock(side_effect=AssertionError('disabled branch must not call native'))
                with patch.object(tsf_input, 'send_text', dead):
                    asyncio.run(TextOutput().output('你好'))
                dead.assert_not_called()
                self.mock_paste.assert_called_once()          # Config.paste=True 默认
                self.mock_write.assert_not_called()
                self.mock_paste.reset_mock()

    def test_native_wrapper_disabled_short_circuits(self):
        ClientConfig.output_mode = 'clipboard'
        dead = MagicMock(side_effect=AssertionError('disabled branch must not call native'))
        with patch.object(tsf_input, 'send_text', dead):
            ok = asyncio.run(native_tsf.output_native('你好'))
        self.assertFalse(ok)
        dead.assert_not_called()

    def test_clipboard_mode_typing_path_unchanged(self):
        ClientConfig.output_mode = 'clipboard'
        dead = MagicMock(side_effect=AssertionError('disabled branch must not call native'))
        with patch.object(tsf_input, 'send_text', dead):
            asyncio.run(TextOutput().output('你好', paste=False))
        dead.assert_not_called()
        self.mock_write.assert_called_once_with('你好')
        self.mock_paste.assert_not_called()


class NativeSuccessTests(RouteTestBase):
    """mode=tsf 成功: 整段一次直插, 不碰剪贴板/键盘"""

    def test_output_routes_once_via_native(self):
        ClientConfig.output_mode = 'tsf'
        fake = FakeSend((tsf_input.ST_INSERTED, ''))
        with patch.object(tsf_input, 'send_text', fake):
            asyncio.run(TextOutput().output('你好'))
        self.assertEqual(fake.calls, ['你好'])
        self.mock_paste.assert_not_called()
        self.mock_write.assert_not_called()

    def test_typing_path_also_routes_native(self):
        ClientConfig.output_mode = 'tsf'
        fake = FakeSend((tsf_input.ST_INSERTED, ''))
        with patch.object(tsf_input, 'send_text', fake):
            asyncio.run(TextOutput().output('你好', paste=False))
        self.assertEqual(fake.calls, ['你好'])
        self.mock_write.assert_not_called()

    def test_carry_punc_prefix_preserved_and_used_once(self):
        ClientConfig.output_mode = 'tsf'
        real_fg = cp._foreground
        real_time = cp.time
        cp._foreground = lambda: 777
        cp.time = types.SimpleNamespace(monotonic=lambda: 200.0)
        self.addCleanup(setattr, cp, '_foreground', real_fg)
        self.addCleanup(setattr, cp, 'time', real_time)
        cp._memo = ('。', 777, 199.0)                 # 上一句同窗口删了句号
        cp.arm()
        fake = FakeSend((tsf_input.ST_INSERTED, ''))
        with patch.object(tsf_input, 'send_text', fake):
            asyncio.run(TextOutput().output('第二句'))
            asyncio.run(TextOutput().output('第三句'))
        self.assertEqual(fake.calls, ['。第二句', '第三句'])   # 前缀只用一次


class NativeFailureTests(RouteTestBase):
    """mode=tsf 失败/结果未知: 单次发送, 弹既有胶囊警告, 不回退剪贴板/键盘"""

    def test_failure_no_retry_no_fallback_with_hint(self):
        statuses = (tsf_input.ST_NO_BRIDGE, tsf_input.ST_FOCUS_MISMATCH, tsf_input.ST_NO_CONTEXT,
                    tsf_input.ST_BUSY, tsf_input.ST_INTERNAL, tsf_input.ST_INVALID,
                    tsf_input.ST_UNKNOWN, tsf_input.ST_UNAVAILABLE)
        for status in statuses:
            with self.subTest(status=status):
                ClientConfig.output_mode = 'tsf'
                fake = FakeSend((status, 'detail-x'))
                hint = MagicMock()
                with (patch.object(tsf_input, 'send_text', fake),
                      patch('core.client.ui.recording_toast.hint_capsule', hint)):
                    asyncio.run(TextOutput().output('你好'))
                self.assertEqual(fake.calls, ['你好'])            # 恰好一次, 无重试
                hint.assert_called_once()
                self.assertIn('历史记录', hint.call_args[0][0])   # 文本留在历史, 提示去复制
                self.assertIs(hint.call_args.kwargs.get('ok'), False)
                self.mock_paste.assert_not_called()               # 不回退剪贴板
                self.mock_write.assert_not_called()               # 不回退键盘
                self.mock_paste.reset_mock()
                self.mock_write.reset_mock()

    def test_native_wrapper_returns_false_on_failure(self):
        ClientConfig.output_mode = 'tsf'
        fake = FakeSend((tsf_input.ST_FOCUS_MISMATCH, 'x'))
        with (patch.object(tsf_input, 'send_text', fake),
              patch('core.client.ui.recording_toast.hint_capsule', MagicMock())):
            ok = asyncio.run(native_tsf.output_native('你好'))
        self.assertFalse(ok)

    def test_native_exception_becomes_unconfirmed_and_does_not_escape(self):
        """ctypes/传输层意外异常收口为「结果未确认」, 不外逃跳过上层历史记录"""
        ClientConfig.output_mode = 'tsf'
        boom = MagicMock(side_effect=OSError('win32 blew up'))
        hint = MagicMock()
        with (patch.object(tsf_input, 'send_text', boom),
              patch('core.client.ui.recording_toast.hint_capsule', hint)):
            ok = asyncio.run(native_tsf.output_native('你好'))
        self.assertFalse(ok)
        hint.assert_called_once()
        self.assertIn('未确认', hint.call_args[0][0])
        self.mock_paste.assert_not_called()
        self.mock_write.assert_not_called()


class LlmRoutingTests(RouteTestBase):
    """LLM 路径: tsf 模式整段缓冲一次性直插 (paste=True/False 都不走流式键盘), 失败不回退"""

    def test_native_mode_buffers_whole_response(self):
        ClientConfig.output_mode = 'tsf'
        handler = _FakeHandler()
        fake = FakeSend((tsf_input.ST_INSERTED, ''))
        with patch.object(tsf_input, 'send_text', fake):
            result = asyncio.run(lot.handle_typing_mode(
                handler, '原始文本', paste=False, matched_hotwords=[],
                role_config=_FakeRole(), content='原始文本'))
        self.assertEqual(result[0], '润色结果')
        self.assertIsNone(handler.got_stream_cb)          # 没有传流式回调 -> 不逐字写出
        self.assertEqual(fake.calls, ['润色结果'])        # 整段一次
        self.mock_write.assert_not_called()               # 无键盘兜底
        self.mock_paste.assert_not_called()

    def test_native_mode_paste_flag_does_not_change_transport(self):
        ClientConfig.output_mode = 'tsf'
        handler = _FakeHandler()
        fake = FakeSend((tsf_input.ST_INSERTED, ''))
        with patch.object(tsf_input, 'send_text', fake):
            asyncio.run(lot.handle_typing_mode(
                handler, '原始文本', paste=True, matched_hotwords=[],
                role_config=_FakeRole(), content='原始文本'))
        self.assertEqual(fake.calls, ['润色结果'])
        self.mock_paste.assert_not_called()

    def test_llm_error_fallback_text_routes_native_once(self):
        """LLM 透传/降级的共用出口 output_text 在 tsf 模式也走原生一次"""
        ClientConfig.output_mode = 'tsf'
        fake = FakeSend((tsf_input.ST_INSERTED, ''))
        with patch.object(tsf_input, 'send_text', fake):
            asyncio.run(lot.output_text('透传文本', paste=True))
        self.assertEqual(fake.calls, ['透传文本'])
        self.mock_paste.assert_not_called()

    def test_clipboard_mode_streaming_unchanged(self):
        """非 tsf 模式: 流式打字路径保持原行为 (传回调 + keyboard.write), 不触原生"""
        ClientConfig.output_mode = 'clipboard'
        handler = _FakeHandler()
        dead = MagicMock(side_effect=AssertionError('clipboard mode must not call native'))
        with patch.object(tsf_input, 'send_text', dead):
            result = asyncio.run(lot.handle_typing_mode(
                handler, '原始文本', paste=False, matched_hotwords=[],
                role_config=_FakeRole(), content='原始文本'))
        dead.assert_not_called()
        self.assertNotEqual(handler.got_stream_cb, None)  # 流式回调照常传入
        self.mock_write.assert_called_once()              # 空流降级走一次 keyboard.write
        self.mock_paste.assert_not_called()

    def test_clipboard_mode_paste_unchanged(self):
        ClientConfig.output_mode = 'clipboard'
        handler = _FakeHandler()
        dead = MagicMock(side_effect=AssertionError('clipboard mode must not call native'))
        with patch.object(tsf_input, 'send_text', dead):
            asyncio.run(lot.handle_typing_mode(
                handler, '原始文本', paste=True, matched_hotwords=[],
                role_config=_FakeRole(), content='原始文本'))
        dead.assert_not_called()
        self.mock_paste.assert_called_once()
        self.mock_write.assert_not_called()


class PersistenceTests(RouteTestBase):
    def test_mode_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / 'user_state.json'
            with patch.object(self.modules.state, 'STATE_FILE', state_file):
                self.assertTrue(self.modules.state.set_output_mode('tsf'))
            self.assertEqual(json.loads(state_file.read_text(encoding='utf-8'))['output_mode'], 'tsf')

    def test_failed_save_preserves_active_mode_and_reports_failure(self):
        ClientConfig.output_mode = 'clipboard'
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / 'missing' / 'user_state.json'
            with patch.object(self.modules.state, 'STATE_FILE', state_file):
                ctx = self.modules.context.Context(directory)
                feedback = []
                ctx.on_saved = lambda ok, text: feedback.append(ok)
                self.assertFalse(ctx.do('set_output_mode', 'tsf'))
            self.assertEqual(ClientConfig.output_mode, 'clipboard')
            self.assertEqual(feedback, [False])

    def test_preview_mode_does_not_modify_config_or_disk(self):
        ClientConfig.output_mode = 'clipboard'
        with tempfile.TemporaryDirectory() as directory:
            ctx = self.modules.context.Context(directory, readonly=True)
            self.assertTrue(ctx.do('set_output_mode', 'tsf'))
            self.assertEqual(ctx.state()['output_mode'], 'tsf')
            self.assertEqual(ClientConfig.output_mode, 'clipboard')
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_invalid_loaded_mode_falls_back_to_clipboard(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / 'user_state.json'
            state_file.write_text('{\"output_mode\":\"invalid\"}', encoding='utf-8')
            with patch.object(self.modules.state, 'STATE_FILE', state_file):
                self.modules.state.load()
            self.assertEqual(ClientConfig.output_mode, 'clipboard')


if __name__ == '__main__':
    unittest.main(verbosity=2)
