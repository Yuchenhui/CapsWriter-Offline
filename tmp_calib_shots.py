"""临时脚本 (2026-10-18): 离屏渲染新麦克风校准窗口并截图自查.

core.client.__init__ / core.client.ui.__init__ 会拉起整个客户端 (websockets / sounddevice / pystray),
开发用的 Python 3.12 没装; 这里把它们换成只带 __path__ 的空壳包, 让目标模块按文件单独加载.
"""
import importlib.util
import runpy
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SHOTS = ROOT / 'tmp_shots_calib'
SHOTS.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT))

import core                      # noqa: E402  (core/__init__ 只依赖 rich + colorama)


def shell(name, path):
    pkg = types.ModuleType(name)
    pkg.__path__ = [str(ROOT / path)]
    sys.modules[name] = pkg
    return pkg


shell('core.client', 'core/client')
shell('core.client.ui', 'core/client/ui')
shell('core.ui', 'core/ui')      # core.ui/__init__ 会拉 pystray / markdown, 这里只要 recording_level


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    parent, _, leaf = name.rpartition('.')
    setattr(sys.modules[parent], leaf, m)
    return m


load('core.ui.recording_level', 'core/ui/recording_level.py')

spec = importlib.util.spec_from_file_location('core.client.ui.calib_window', ROOT / 'core/client/ui/calib_window.py')
cw = importlib.util.module_from_spec(spec)
sys.modules['core.client.ui.calib_window'] = cw
sys.modules['core.client.ui'].calib_window = cw
spec.loader.exec_module(cw)

cal_spec = importlib.util.spec_from_file_location('core.client.calibration', ROOT / 'core/client/calibration.py')
cal = importlib.util.module_from_spec(cal_spec)
sys.modules['core.client.calibration'] = cal
sys.modules['core.client'].calibration = cal
cal_spec.loader.exec_module(cal)

sys.argv = ['calib_window', '--preview', '--shots', str(SHOTS), '--theme', 'frost']
try:
    runpy.run_module('core.client.ui.calib_window', run_name='__main__', alter_sys=True)
except BaseException as e:      # 截图脚本要看得见失败原因, 否则只剩下一个不退出的 mainloop
    import traceback
    traceback.print_exc()
    raise SystemExit(1)
print('截图:', sorted(p.name for p in SHOTS.glob('*.png')))
