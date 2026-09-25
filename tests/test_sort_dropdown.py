# coding: utf-8
"""设置窗口: 拖拽排序列表 (模拟鼠标拖动) + 下拉选择 + 候补默认排序. 需要桌面 (Tk)"""
import tkinter as tk

from core.client.settings.palette import for_theme
from core.client.settings.pages.engine import _fallback_items
from core.client.settings.widgets import Dropdown, SortList

root = tk.Tk()
pal = for_theme('frost')
items = [{'key': k, 'title': k, 'detail': '', 'local': k.startswith('local:')} for k in ('a', 'b', 'c', 'local:x')]
got = []
sl = SortList(root, pal, items, got.append)
sl.pack(fill='x')
root.update()


class Ev:
    def __init__(self, y):
        self.y_root = sl.winfo_rooty() + y


h = sl.rows['a'].winfo_height()
sl._start('c')                       # 把第 3 行拖到最上面
sl._move(Ev(h * 0.2))
sl._end(None)
assert got[-1] == ['c', 'a', 'b', 'local:x'], got
sl._start('c')                       # 再拖到最下面
sl._move(Ev(h * 3.9))
sl._end(None)
assert got[-1] == ['a', 'b', 'local:x', 'c'], got
assert str(sl.rows['c'].title.cget('fg')) == pal.muted, '本地之后的行应变淡'

picked = []
dd = Dropdown(root, pal, [{'key': 'p', 'title': 'P', 'group': 'g'}, {'key': 'q', 'title': 'Q', 'group': 'g'}], 'p', picked.append)
dd.pack(fill='x')
root.update()
dd.toggle()
assert dd.pop is not None
dd._pick('q')
assert picked == ['q'] and dd.key == 'q' and dd.pop is None
dd._pick('q')                        # 选同一个不重复回调
assert picked == ['q']

# 候补默认排序: 保存过的在前; 其余云端按评分, 当前加载的本地排本地第一, 主力与流式不出现
all_items = [{'key': 'qwen-stream', 'kind': 'stream', 'detail': '· 10.3/12'},
             {'key': 'mimo-batch', 'kind': 'batch', 'detail': '套餐内 · 10.0/12'},
             {'key': 'qwen-batch', 'kind': 'batch', 'detail': '¥0.79/小时 · 10.7/12'},
             {'key': 'step-batch', 'kind': 'batch', 'detail': '¥0.15/小时 · 10.5/12'},
             {'key': 'local:sensevoice', 'kind': 'local', 'detail': '免费 · 5.0/12'},
             {'key': 'local:qwen_asr', 'kind': 'local', 'detail': '免费 · 9.0/12'}]
order = [i['key'] for i in _fallback_items(all_items, 'step-batch', [], 'qwen_asr')]
assert order == ['qwen-batch', 'mimo-batch', 'local:qwen_asr', 'local:sensevoice'], order
order = [i['key'] for i in _fallback_items(all_items, 'step-batch', ['mimo-batch'], 'qwen_asr')]
assert order[0] == 'mimo-batch', order
root.destroy()
print('OK')
