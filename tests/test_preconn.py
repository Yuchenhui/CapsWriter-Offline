# coding: utf-8
"""预连接: 同时最多一条; 取消 / 用完即关; 关得比连得快也不漏; 被服务器断开能重连. 需要联网 (连 api.stepfun.com, 不发识别请求)"""
import socket
import time
import urllib.request

from core.client.audio import cloud_asr as ca
from core.client.audio.cloud_asr import BatchASR, Preconn


def wait(cond, sec=10.0):
    t = time.time()
    while time.time() - t < sec:
        if cond():
            return True
        time.sleep(0.02)
    return False


# 1. 建好一条; 新的一句开始时上一条被关掉, 全局仍只有一条
a = Preconn('api.stepfun.com')
assert wait(lambda: Preconn.open_count() == 1), '预连接没建起来'
b = Preconn('api.stepfun.com')
assert a._closed and not a._conns, '上一条没关'
assert wait(lambda: Preconn.open_count() == 1)
b.close()
assert Preconn.open_count() == 0

# 2. 还没连上就关了 (松开得比 TLS 握手快 / 没说话): 连上后立刻关掉, 不挂着
c = Preconn('api.stepfun.com')
c.close()
time.sleep(1.5)
assert not c._conns and Preconn.open_count() == 0, '关了之后连上的连接没被关掉'

# 3. 取消录音 (判定没说话) 关连接
x = BatchASR('stepaudio-2.5-asr')
assert wait(lambda: x._pre.take is not None and Preconn.open_count() == 1)
x.cancel()
assert Preconn.open_count() == 0

# 4. 连接被服务器断开 (模拟: 直接关掉底层 socket): _open 重建后照常发出, 并登记由 close() 统一关
y = BatchASR('stepaudio-2.5-asr')
assert wait(lambda: Preconn.open_count() == 1)
y._pre._conns[0].sock.shutdown(socket.SHUT_RDWR)
req = urllib.request.Request('https://api.stepfun.com/v1/models', headers={'Authorization': 'Bearer x'})
try:
    y._open(req)
except urllib.error.HTTPError as e:           # 401 = 请求确实发到了服务器
    assert e.code in (401, 403), e.code
assert len(y._pre._conns) == 2, '重建的连接没登记'
y.cancel()
assert Preconn.open_count() == 0
print('OK')
