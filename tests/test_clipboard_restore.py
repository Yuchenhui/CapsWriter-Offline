"""语音粘贴保留原剪贴板的关键分支，不触碰系统剪贴板。"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from config_client import ClientConfig
from core.client.clipboard import clipboard as cb


class ClipboardRestoreTests(unittest.TestCase):
    def setUp(self):
        for target, attribute in ((cb.keyboard, 'Controller'), (cb._keyboard, 'write'), (cb.pyclip, 'copy')):
            stub = patch.object(target, attribute)
            stub.start()
            self.addCleanup(stub.stop)

    def test_voice_result_remains_available_for_delayed_paste(self):
        for original in ((True, '', 7), (False, '原来复制的文字', 7)):
            with self.subTest(original=original[1]):
                clipboard = {'text': original[1], 'seq': 7}

                def write(text, exclude_history=True):
                    clipboard.update(text=text, seq=8)
                    return 8

                def restore(snapshot):
                    clipboard.update(text=snapshot[1], seq=9)

                with (patch.object(cb.platform, 'system', return_value='Windows'),
                      patch.object(cb, 'plain_text_snapshot', return_value=original),
                      patch.object(cb, 'clipboard_seq', side_effect=lambda: clipboard['seq']),
                      patch.object(cb, 'set_clipboard_text', side_effect=write) as copy,
                      patch.object(cb, 'restore_plain_text', side_effect=restore),
                      patch.object(cb.asyncio, 'sleep', new_callable=AsyncMock),
                      patch.object(cb.keyboard, 'Controller', return_value=MagicMock())):
                    asyncio.run(cb.paste_text('识别结果', restore_clipboard=ClientConfig.restore_clip))
                self.assertEqual(clipboard['text'], '识别结果')
                copy.assert_called_once_with('识别结果', exclude_history=True)

    def test_plain_text_is_restored_after_paste(self):
        original = (False, '原来复制的文字', 7)
        with (patch.object(cb.platform, 'system', return_value='Windows'),
              patch.object(cb, 'plain_text_snapshot', return_value=original),
              patch.object(cb, 'clipboard_seq', side_effect=[7, 8]),
              patch.object(cb, 'set_clipboard_text', return_value=8),
              patch.object(cb, 'restore_plain_text') as restore,
              patch.object(cb.asyncio, 'sleep', new_callable=AsyncMock),
              patch.object(cb.keyboard, 'Controller', return_value=MagicMock())):
            asyncio.run(cb.paste_text('识别结果'))
        restore.assert_called_once_with(original)

    def test_rich_clipboard_is_never_overwritten(self):
        with (patch.object(cb.platform, 'system', return_value='Windows'),
              patch.object(cb, 'plain_text_snapshot', return_value=None),
              patch.object(cb, 'set_clipboard_text') as write,
              patch.object(cb._keyboard, 'write') as type_text):
            asyncio.run(cb.paste_text('识别结果', restore_clipboard=False))
        write.assert_not_called()
        type_text.assert_called_once_with('识别结果')

    def test_rich_clipboard_multiline_is_not_typed(self):
        with (patch.object(cb.platform, 'system', return_value='Windows'),
              patch.object(cb, 'plain_text_snapshot', return_value=None),
              patch.object(cb._keyboard, 'write') as type_text,
              patch('core.client.ui.recording_toast.hint_capsule') as hint):
            asyncio.run(cb.paste_text('1. 第一项\n2. 第二项', restore_clipboard=False))
        type_text.assert_not_called()
        hint.assert_called_once()
        self.assertIn('历史', hint.call_args.args[0])

    def test_write_failure_multiline_is_not_typed(self):
        with (patch.object(cb.platform, 'system', return_value='Windows'),
              patch.object(cb, 'plain_text_snapshot', return_value=(False, '旧文字', 7)),
              patch.object(cb, 'set_clipboard_text', side_effect=OSError('busy')),
              patch.object(cb._keyboard, 'write') as type_text,
              patch('core.client.ui.recording_toast.hint_capsule') as hint):
            asyncio.run(cb.paste_text('1. 第一项\n2. 第二项', restore_clipboard=False))
        type_text.assert_not_called()
        hint.assert_called_once()

    def test_private_write_failure_does_not_use_public_clipboard(self):
        with (patch.object(cb.platform, 'system', return_value='Windows'),
              patch.object(cb, 'plain_text_snapshot', return_value=(True, '', 7)),
              patch.object(cb, 'clipboard_seq', return_value=7),
              patch.object(cb, 'set_clipboard_text', side_effect=OSError('busy')),
              patch.object(cb.pyclip, 'copy') as public_copy,
              patch.object(cb._keyboard, 'write') as type_text):
            asyncio.run(cb.paste_text('识别结果', restore_clipboard=False))
        public_copy.assert_not_called()
        type_text.assert_called_once_with('识别结果')

    def test_non_text_format_is_not_mistaken_for_plain_text(self):
        with (patch.object(cb, '_u32') as win,
              patch.object(cb, 'clipboard_seq', return_value=7),
              patch.object(cb.pyclip, 'paste') as read):
            win.OpenClipboard.return_value = 1
            win.EnumClipboardFormats.side_effect = [13, 493, 0]  # Unicode + HTML
            win.RegisterClipboardFormatW.side_effect = [100, 101, 102]
            self.assertIsNone(cb.plain_text_snapshot())
        read.assert_not_called()

    def test_user_clipboard_change_is_not_overwritten(self):
        with (patch.object(cb.platform, 'system', return_value='Windows'),
              patch.object(cb, 'plain_text_snapshot', return_value=(False, '旧文字', 7)),
              patch.object(cb, 'clipboard_seq', side_effect=[7, 9]),
              patch.object(cb, 'set_clipboard_text', return_value=8),
              patch.object(cb, 'restore_plain_text') as restore,
              patch.object(cb.asyncio, 'sleep', new_callable=AsyncMock),
              patch.object(cb.keyboard, 'Controller', return_value=MagicMock())):
            asyncio.run(cb.paste_text('识别结果'))
        restore.assert_not_called()


if __name__ == '__main__':
    unittest.main()
