# coding: utf-8
"""云端识别失败提示: 错误分类 + 同类 10 分钟只弹一次"""
import io
import sys
import types
import urllib.error

from core.client.audio import cloud_asr as ca

shown = []
sys.modules['core.ui.toast'] = types.SimpleNamespace(toast=lambda text, **kw: shown.append(text))   # 不真弹窗


def http(code, body):
    return urllib.error.HTTPError('u', code, 'x', {}, io.BytesIO(body.encode()))


assert ca.classify_exc(http(429, '{"error":{"code":"1113","message":"余额不足或无可用资源包,请充值。"}}'))[0] == 'balance'
assert ca.classify_exc(http(429, 'Too Many Requests'))[0] == 'rate'
assert ca.classify_exc(http(401, 'Unauthorized'))[0] == 'auth'
assert ca.classify(45000030, '[resource_id=volc.bigasr.auc_turbo] requested resource not granted') == 'auth'
assert ca.classify_exc(TimeoutError())[0] == 'timeout'
assert ca.classify_exc(ConnectionResetError())[0] == 'net'
assert ca.classify(500, 'internal') == 'error'

ca.Config.asr_engine = 'zhipu-batch'
ca.alert('balance')
ca.alert('balance')                     # 10 分钟内同类不再弹
ca.alert('timeout')                     # 不同类照弹
assert len(shown) == 2, shown
assert shown[0].startswith('智谱 glm-asr-2512：余额不足') and '本地识别' in shown[0], shown[0]
print('OK')
