"""语音粘贴保留原剪贴板的关键分支，不触碰系统剪贴板。"""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from config_client import ClientConfig
from core.client.clipboard import clipboard as cb


class ClipboardRestoreTests(unittest.TestCase):
    def test_restore_is_enabled(self):
        self.assertTrue(ClientConfig.restore_clip)

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
            asyncio.run(cb.paste_text('识别结果'))
        write.assert_not_called()
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
