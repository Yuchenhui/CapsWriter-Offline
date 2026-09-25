"""托盘状态行自检: python tests/test_tray_status.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config_client import ClientConfig as C
from core.client.manager import tray_manager as tm

C.asr_engine, C.polish = 'qwen-batch', ''
lines = tm.status_lines()
assert lines[0] == '识别：千问 qwen3-asr-flash（在线）', lines
assert lines[1] == '二次整理：关', lines
assert lines[2].startswith('今日花费：'), lines
C.asr_engine, C.polish = 'local', 'deepseek'
lines = tm.status_lines()
assert lines[0].startswith('识别：本地') and lines[1] == '二次整理：DeepSeek V4 Flash', lines
C.asr_engine = 'cloud'                                   # 旧配置值
assert tm.status_lines()[0] == '识别：千问 qwen-audio-3.1（在线 · 流式）', tm.status_lines()
print('OK')
