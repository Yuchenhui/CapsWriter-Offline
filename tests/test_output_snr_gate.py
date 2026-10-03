"""只读加载真实 SNR 判据做离线回归；不导入 UI、键盘钩子或启动识别程序。"""
import ast
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
LEVEL_PATH = ROOT / 'core/client/audio/level.py'
OUTPUT_PATH = ROOT / 'core/client/output/result_processor.py'

spec = importlib.util.spec_from_file_location('_capswriter_level_regression', LEVEL_PATH)
level = importlib.util.module_from_spec(spec)
spec.loader.exec_module(level)
TREE = ast.parse(OUTPUT_PATH.read_text(encoding='utf-8'))
PROCESSOR = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == 'ResultProcessor')
HANDLER = next(node for node in PROCESSOR.body if isinstance(node, ast.AsyncFunctionDef) and node.name == '_handle_message')
GATE = next(node for node in ast.walk(HANDLER) if isinstance(node, ast.If)
            and any(isinstance(part, ast.Name) and part.id == '_snr' for part in ast.walk(node.test)))
PREDICATE = compile(ast.Expression(GATE.test), str(OUTPUT_PATH), 'eval')


def output_rejects(snr):
    # 执行生产代码里的原始条件，不复制一份判据，不加载有副作用的完整客户端。
    return bool(eval(PREDICATE, {'_snr': snr, 'SNR_MIN': level.SNR_MIN}))


class OutputSnrGateTests(unittest.TestCase):
    def test_shared_constant_is_imported(self):
        imports = [node for node in TREE.body if isinstance(node, ast.ImportFrom)
                   and node.module == 'core.client.audio.level']
        self.assertTrue(any(alias.name == 'SNR_MIN' for node in imports for alias in node.names),
                        '输出层必须引用录音层的共享 SNR_MIN，不能另写一个门槛')

    def test_logged_11_1_db_case_is_not_rejected(self):
        blocks = [-60.0] * 10 + [-48.9] * 10
        self.assertFalse(level.is_silence(0.001, blocks, 0.0056))
        self.assertFalse(output_rejects(11.1), '日志里已有 14 字结果且 VAD 通过的 11.1 dB 不应被 12 dB 旧门槛拒绝')

    def test_shared_boundary_and_values_below_12(self):
        threshold = level.SNR_MIN
        for snr in (threshold, threshold + 0.1, 11.1, 11.9, 12.0, 20.0):
            with self.subTest(snr=snr):
                self.assertFalse(output_rejects(snr))
        self.assertTrue(output_rejects(threshold - 0.1))

    def test_very_low_snr_is_still_rejected(self):
        self.assertTrue(output_rejects(0.9), '本次不关闭过滤，也不假称修复最新 0.9 dB 那句')
        self.assertTrue(output_rejects(7.0))

    def test_missing_measurement_remains_fail_open(self):
        for snr in (None, 'unknown', float('nan')):
            with self.subTest(snr=snr):
                self.assertFalse(output_rejects(snr))


if __name__ == '__main__':
    unittest.main(verbosity=2)
