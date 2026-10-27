"""语音诊断样本: 原始 ASR、整理结果、上屏文本和原始录音必须能按 task_id 关联。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config_client import ClientConfig as Config
from core.client import history, user_state
from core.protocol import RecognitionMessage
from core.server.schema import Result, Task
from core.server.worker import pipeline as pipeline_mod


class VoiceDiagnosticsTests(unittest.TestCase):
    def test_raw_text_round_trips_and_old_messages_default_empty(self):
        msg = RecognitionMessage(
            task_id='task-1', is_final=True, duration=1.2, time_start=1.0,
            time_submit=2.0, time_complete=2.1, text='整理后的文字', raw_text='原始识别',
        )
        self.assertEqual(RecognitionMessage.from_dict(msg.to_dict()).raw_text, '原始识别')

        old = msg.to_dict()
        old.pop('raw_text')
        self.assertEqual(RecognitionMessage.from_dict(old).raw_text, '')

    def test_finalize_keeps_raw_text_before_polish(self):
        result = Result(task_id='task-2', socket_id='socket', type='mic', text='原始识别')
        task = Task(
            type='mic', data=b'', offset=0.0, overlap=0.0, task_id='task-2',
            socket_id='socket', is_final=True, time_start=1.0, time_submit=2.0,
            polish='deepseek', window='', structure=False,
        )
        pipe = pipeline_mod.TaskPipeline.__new__(pipeline_mod.TaskPipeline)
        pipe.formatter = SimpleNamespace(format=lambda text, skip_num=False: text)

        def fake_polish(text, choice=True, window='', structure=False, task_id=''):
            return '整理后的文字', True

        with patch.object(pipeline_mod, 'polish_ex', side_effect=fake_polish) as polish:
            final = pipe._finalize(task, result)

        self.assertEqual(final.raw_text, '原始识别')
        self.assertEqual(final.text, '整理后的文字')
        polish.assert_called_once_with('原始识别', 'deepseek', '', False, 'task-2')

    def test_plain_polish_rejects_unapproved_content_deletion(self):
        from core.server.worker import polish
        with patch.object(polish.Config, 'polish_max_change', 1.0):
            self.assertFalse(polish._plain_ok('请把红色的文件发给小王', '请把文件发给小王'))
            self.assertTrue(polish._plain_ok('请把红色的文件发给小王', '请把红色的文件发给小王'))

    def test_capture_writes_linkable_manifest_and_stops_at_zero(self):
        self.assertTrue(hasattr(history, 'complete_capture_sample'))
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(Config, 'diagnostic_capture_remaining', 2, create=True), \
                patch.object(user_state, 'save') as save:
            sample = {
                'task_id': 'task-3',
                'audio_path': Path(tmp) / 'original.wav',
                'raw_text': '原始识别',
                'polished_text': '整理后的文字',
                'output_text': '最终上屏',
                'engine': 'local',
                'polish': 'deepseek',
                'structure': False,
                'gain_db': 15.5,
            }
            self.assertEqual(history.complete_capture_sample(tmp, **sample), 1)
            self.assertEqual(history.complete_capture_sample(tmp, **sample), 0)
            self.assertEqual(history.complete_capture_sample(tmp, **sample), 0)

            rows = [
                json.loads(line)
                for line in (Path(tmp) / history.SAMPLE_FILE_NAME).read_text(encoding='utf-8').splitlines()
            ]

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['task_id'], 'task-3')
        self.assertEqual(rows[0]['raw_text'], '原始识别')
        self.assertEqual(rows[0]['polished_text'], '整理后的文字')
        self.assertEqual(rows[0]['output_text'], '最终上屏')
        self.assertEqual(rows[0]['audio'], str(Path(tmp) / 'original.wav'))
        self.assertEqual(save.call_count, 2)


if __name__ == '__main__':
    unittest.main()
