"""麦克风设备真值 + 校准会话测试: 桩替换 COM / 音频流, 不碰真实麦克风. 运行: python tests/test_mic_device_truth.py"""
import importlib.util
import logging
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _stub_pkg(name):
    sys.modules[name] = types.ModuleType(name)


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


for _n in ('core', 'core.client', 'core.client.audio'):
    _stub_pkg(_n)
sys.modules['core.client.audio'].logger = logging.getLogger('t')

# ---- 设备真值: 音频流报的设备名 vs Windows 默认端点 ----
DEVS = [('id-atk', 'Microphone (ATK N9V2 ULTIMATE dongle)', False, 2.0),
        ('id-jabra', 'Microphone (Jabra UC VOICE 250a)', False, 5.0),
        ('id-uu', 'Microphone Array (UUYC Virtual Audio Device)', False, 0.0)]

ddw = load('core.client.audio.default_device_watch', 'core/client/audio/default_device_watch.py')
DEFAULT_ID = {'v': 'id-jabra'}
ddw.default_capture_id = lambda: DEFAULT_ID['v']      # 真 COM 调用太慢/会动系统默认, 这里换成查表

mic_select = load('core.client.audio.mic_select', 'core/client/audio/mic_select.py')

# 1. WASAPI 会截断设备名, 按前缀匹配; 重名后缀不算另一个设备
assert mic_select.name_matches('Microphone (ATK N9V2 ULTIMATE d', 'Microphone (ATK N9V2 ULTIMATE dongle)')
assert mic_select.name_matches('Microphone (Jabra UC VOICE 250a) (2)', 'Microphone (Jabra UC VOICE 250a)')
assert not mic_select.name_matches('Microphone (Jabra UC VOICE 250a)', 'Microphone (ATK N9V2 ULTIMATE dongle)')
assert not mic_select.name_matches('', 'Microphone (Jabra UC VOICE 250a)')

# 2. 反查: 截断名 -> Windows 端点 id; 不在列表里返回 None
assert mic_select.resolve_capture('Microphone (ATK N9V2 ULTIMATE d', DEVS)[0] == 'id-atk'
assert mic_select.resolve_capture('Microphone (Jabra UC VOICE 250a', DEVS)[0] == 'id-jabra'
assert mic_select.resolve_capture('Microphone (Some New Dongle)', DEVS) is None

# ---- 校准会话 ----
cfg = types.ModuleType('config_client')
cfg.ClientConfig = types.SimpleNamespace(polish='structure', mic_auto=True)
sys.modules['config_client'] = cfg

gain_state = {'db': 5.0, 'min': -15.0, 'max': 5.0, 'inc': 2.0, 'set': []}
mic_select.list_capture = lambda: [tuple(d) for d in DEVS]
mic_select.gain_info = lambda dev_id: (gain_state['db'], gain_state['min'], gain_state['max'], gain_state['inc'])


def _set_gain(dev_id, db):
    gain_state['set'].append((dev_id, db))
    gain_state['db'] = db
    return True


mic_select.set_gain = _set_gain
cal = load('core.client.calibration', 'core/client/calibration.py')
# from X import Y 走的是父包属性, 光塞 sys.modules 不够 (否则会重新按文件导入一份, 桩就失效了)
sys.modules['core.client.audio'].mic_select = mic_select
sys.modules['core.client.audio'].default_device_watch = ddw
sys.modules['core.client'].calibration = cal

STREAM = {'name': 'Microphone (ATK N9V2 ULTIMATE d'}
app = types.SimpleNamespace(
    stream=types.SimpleNamespace(current_device_name=lambda: STREAM['name']),
    state=types.SimpleNamespace(last_level={'avg': -34.0, 'peak': -12.0, 'snr': 27.0}))


def reset():
    cal._s, cal._note = None, ''
    gain_state.update(db=5.0)
    gain_state['set'].clear()
    STREAM['name'] = 'Microphone (ATK N9V2 ULTIMATE d'
    DEFAULT_ID['v'] = 'id-jabra'
    cfg.ClientConfig.polish, cfg.ClientConfig.mic_auto = 'structure', True
    app.state.last_level = {'avg': -34.0, 'peak': -12.0, 'snr': 27.0}


# 3. 绑的是正在录的设备 (ATK), 不是 Windows 默认端点 (Jabra)
reset()
assert cal.start(app)
s = cal.snapshot()
assert s['dev_name'] == 'Microphone (ATK N9V2 ULTIMATE dongle)', s['dev_name']
assert cfg.ClientConfig.mic_auto is False, '校准期间应暂停自动切换'
assert cfg.ClientConfig.polish == '', '校准期间关掉二次整理'

# 4. 六句跑完: 出成绩. 峰值 -2 (贴 0 未削顶) -> 目标 -6 -> 降 4 dB, 步长 2 -> 5 -> 1
for i in range(len(cal.SENTENCES)):
    app.state.last_level = {'avg': -34.0, 'peak': -2.0, 'snr': 27.0}
    cal.on_result(cal.SENTENCES[i][1])
assert cal.snapshot()['done'] is True
res = cal.snapshot()['result']
assert gain_state['set'] == [('id-atk', 1.0)], gain_state['set']
assert res['gain_before'] == 5.0 and res['gain_now'] == 1.0 and res['applied'] is True
assert len(cal.snapshot()['rows']) == len(cal.SENTENCES)

# 5. 校准中换设备: 成绩清空重头来, 增益基准跟着换 (旧成绩对不上新设备)
reset()
cal.start(app)
cal.on_result(cal.SENTENCES[0][1])
STREAM['name'] = 'Microphone (Jabra UC VOICE 250a)'
DEFAULT_ID['v'] = 'id-atk'                       # 默认端点也换, 免得走"读不到"分支
app.state.last_level = {'avg': -30.0, 'peak': -20.0, 'snr': 20.0}
cal.on_result(cal.SENTENCES[1][1])
s = cal.snapshot()
assert s['dev_name'] == 'Microphone (Jabra UC VOICE 250a)', s['dev_name']
assert s['idx'] == 0 and not s['rows'], s
assert 'Jabra' in s['note'] and s['gain'] == 5.0, s

# 6. 取消: 恢复二次整理与自动切换, 增益不动
assert cal.cancel() is True
assert cal.snapshot() == {'active': False}
assert cfg.ClientConfig.polish == 'structure' and cfg.ClientConfig.mic_auto is True
assert gain_state['set'] == []
assert '已结束' in cal.last_note()
assert cal.cancel() is False, '没有会话时取消应返回 False'

# 7. 恢复校准前的增益 (成绩页的"恢复"按钮)
reset()
cal.start(app)
for sentence in cal.SENTENCES:
    app.state.last_level = {'avg': -34.0, 'peak': -2.0, 'snr': 27.0}
    cal.on_result(sentence[1])
assert cal.restore_gain() is True
assert gain_state['set'][-1] == ('id-atk', 5.0), gain_state['set']
assert cal.snapshot()['result']['undone'] is True

# 8. 没说话: 增益往上一档 (夹在设备上限内)
reset()
gain_state['db'] = -9.0
cal.start(app)
cal.on_silence()
assert cal.snapshot()['gain'] == -7.0, cal.snapshot()['gain']
assert gain_state['set'] == [('id-atk', -7.0)], gain_state['set']
assert '音量太小' in cal.snapshot()['note']

# 9. 读不到设备名时退回 Windows 默认端点, 不让校准直接打不开
reset()
STREAM['name'] = ''
DEFAULT_ID['v'] = 'id-jabra'
assert cal.start(app)
assert cal.snapshot()['dev_name'] == 'Microphone (Jabra UC VOICE 250a)'
cal.cancel()

print('mic device truth / calibration selftest ok')
