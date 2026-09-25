# coding: utf-8
"""云端识别失败提示: 错误分类 + 同类 10 分钟只弹一次"""
import io
import sys
import types
import urllib.error

from core.client.audio import cloud_asr as ca

shown = []
import core.ui.live_bubble as lb
lb.notice = lambda text, seconds=4.0: shown.append(text)                      # 不真显示


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
ca.alert('balance')                     # 余额类: 之后每句一行短提示
ca.alert('timeout')                     # 超时: 首次提示
ca.alert('timeout')                     # 超时: 10 分钟内不再提示
assert len(shown) == 3, shown
assert shown[1] == '智谱不可用（余额不足），这句改用了本地识别', shown[1]
assert shown[0].startswith('智谱：余额不足') and '本地识别' in shown[0], shown[0]
print('OK')

# 句末补标点
assert ca.end_punc('看一下它效果怎么样') == '看一下它效果怎么样。'
assert ca.end_punc('能用吗') == '能用吗？'
assert ca.end_punc('版本号 v2.3.1') == '版本号 v2.3.1。'
assert ca.end_punc('看看 Cloud Code') == '看看 Cloud Code。'
for done in ('好的。', '真的？', '走！', '列表：', '(注)', ''):
    assert ca.end_punc(done) == done, done
print('end_punc OK')
