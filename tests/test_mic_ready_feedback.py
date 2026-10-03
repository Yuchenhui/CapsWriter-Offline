"""麦克风冷启动与胶囊就绪交接的静态回归；避免加载 Tk/键盘钩子。"""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _tree(path):
    return ast.parse((ROOT / path).read_text(encoding='utf-8'))


def test_audio_callback_announces_ready_after_recording_guard():
    tree = _tree('core/client/audio/stream.py')
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AudioStreamManager')
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_audio_callback')
    source = ast.unparse(fn)
    assert source.index('if not self.state.recording') < source.index('ready_active()')
    assert source.index('ready_active()') < source.index("self.state.queue_in.put")


def test_shortcut_starts_waiting_only_for_cold_microphone():
    source = ast.unparse(_tree('core/client/shortcut/task.py'))
    assert 'mic_ready = self.app.stream._running and (not self.app.stream.is_stale(1.0))' in source
    assert 'self._rec_toast.start(waiting=not mic_ready)' in source


def test_toast_supports_waiting_and_ready_messages():
    source = ast.unparse(_tree('core/ui/toast_recording.py'))
    assert "new_text == 'waiting'" in source
    assert "new_text == 'ready'" in source
    assert "self._mode = 'listening'" in source
    assert '_tint_green' in source
    assert "'waiting': 'recording'" in source
    assert '_tint_amber' in source
