"""设置窗口操作自检: python tests/test_settings_actions.py"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.client.settings import actions
from core.client import user_state
from config_client import ClientConfig as C

saved = []
user_state.save = lambda: saved.append(dict(engine=C.asr_engine, polish=C.polish, theme=C.capsule_theme, st=C.polish_structure))

base = Path(tempfile.mkdtemp())
assert actions.save_wordlist(base, 'terms.txt', 'GUI\nHigh Speed\n')
assert (base / 'terms.txt').read_text(encoding='utf-8') == 'GUI\nHigh Speed\n'
assert not list(base.glob('*.tmp')), '原子写不留临时文件'
assert not actions.save_wordlist(base, '..\\evil.txt', 'x') and not (base.parent / 'evil.txt').exists(), '白名单外拒绝'
assert not actions.save_wordlist(base, 'config_client.py', 'x')

assert actions.set_engine('qwen-batch') and C.asr_engine == 'qwen-batch'
assert not actions.set_engine('nope') and C.asr_engine == 'qwen-batch', '未知引擎拒绝'
assert actions.set_polish('') and C.polish == ''
assert not actions.set_polish('nope')
assert actions.set_structure(False) and C.polish_structure is False
assert actions.set_theme('frost') and C.capsule_theme == 'frost'
assert not actions.set_theme('nope')
assert len(saved) == 4, saved

from core.client.settings.context import Context
ctx = Context(base)                                   # 真实模式: do 分派到 actions, 自动补 base_dir
assert ctx.do('save_wordlist', 'hot.txt', 'a\nb') and (base / 'hot.txt').read_text(encoding='utf-8') == 'a\nb'
assert not ctx.do('save_wordlist', 'x.txt', 'a') and not ctx.do('no_such_action')
ro = Context(base, readonly=True)                     # 预览模式: 不写
assert ro.do('save_wordlist', 'terms.txt', 'CHANGED') and 'CHANGED' not in (base / 'terms.txt').read_text(encoding='utf-8')
print('OK')
