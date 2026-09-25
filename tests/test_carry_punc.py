"""延迟补标点自检: python tests/test_carry_punc.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.client.shortcut.shortcut_manager   # noqa: F401  确认钩子模块 import 链无循环
from core.client.output import carry_punc as cp
from core.client.output.text_output import TextOutput

win = [1]
cp._foreground = lambda: win[0]
clock = [100.0]
cp.time.monotonic = lambda: clock[0]

def say(stripped, text):
    """模拟一句: arm -> 写出 -> remember; 返回实际写出的内容"""
    cp.arm()
    out = cp.prefixed(text)
    cp.prefixed('后续 chunk 不再带前缀')
    cp.remember(stripped)
    clock[0] += 1
    return out

# strip_punc 记录删掉的标点 (短句删, 长句不删)
assert TextOutput.strip_punc('你好。') == '你好' and TextOutput.last_stripped == '。'
assert TextOutput.strip_punc('这是一个超过八个字的比较长的句子。') .endswith('。') and TextOutput.last_stripped == ''
TextOutput.strip_punc('好的，', record=False); assert TextOutput.last_stripped == ''

assert say('。', '第一句') == '第一句'                 # 首句无前缀
assert say('，', '第二句') == '。第二句'               # 接着写: 补上一句的 。
cp.touch(); clock[0] += 1
assert say('。', '第三句') == '第三句'                 # 中间按过键 -> 不补
win[0] = 2
assert say('。', '第四句') == '第四句'                 # 换了窗口 -> 不补
clock[0] += cp._TIMEOUT + 1
assert say('.', 'fifth') == 'fifth'                   # 超时 -> 不补
assert say('', 'sixth') == '. sixth'                  # 英文标点后带空格
assert say('', '第七句') == '第七句'                   # 上一句没删标点 -> 不补
cp.arm(); cp.touch(); cp.remember('。'); clock[0] += 1
assert say('', '第八句') == '第八句'                   # 输出过程中用户动了键盘 -> 不补
print('OK')
