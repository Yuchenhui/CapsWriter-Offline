# coding: utf-8
"""回归: 切换主力后候补 SortList 必须仍在 holder 里 (2026-09-30 复现).

engine.py build 末尾曾用 `items = [...]` 覆盖了引擎列表, 导致 pick 回调再
render_list 时 _fallback_items(items, ...) 拿到 POLISH dict (没有 'kind' 字段),
KeyError -> pick 抛 -> holder 子控件被 destroy 但新 SortList 没创建 -> 候补区空白.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tkinter as tk

from core.client.settings.pages import engine as engine_page
from core.client.settings.palette import for_theme
from core.client.settings.widgets import SortList


class Ctx:
    def __init__(self):
        self.local = 'qwen_asr'
        self.saved_calls = []
    def state(self):
        return {'asr_engine': 'step-plan-batch', 'asr_fallback': ['qwen-batch', 'step-batch'],
                'polish': '', 'polish_structure': False, 'capsule_theme': 'frost'}
    def local_model(self): return self.local
    def local_models(self): return [('qwen_asr', 'qwen_asr', True), ('sensevoice', 'sensevoice', True)]
    def do(self, what, *a):
        self.saved_calls.append((what, a)); return True


root = tk.Tk()
pal = for_theme('frost')
page = engine_page.build(root, pal, Ctx())
page.pack(fill='both', expand=True)
root.update_idletasks()

# 找候补 holder: 它是 page 子 Frame 里唯一装了 SortList 的那个
holder = None
for w in page.winfo_children():
    if isinstance(w, tk.Frame):
        children = w.winfo_children()
        if children and isinstance(children[0], SortList):
            holder = w; break
assert holder is not None, '找不到候补 holder'
sl = holder.winfo_children()[0]
assert isinstance(sl, SortList), f'首屏候补应是 SortList, 拿到 {type(sl).__name__}'
init_n = len(sl.rows)
assert init_n >= 4, f'首屏候补数异常: {init_n}'

# 切主力: 复现 bug. Dropdown._pick 直接调 on_pick (engine.pick).
primary_box = None
for w in page.winfo_children():
    if hasattr(w, '_pick') and hasattr(w, 'items'):   # Dropdown
        primary_box = w; break
assert primary_box is not None
primary_box._pick('qwen-batch')
root.update_idletasks()

# 关键: 切完之后候补区不能空白
sl_after = [w for w in holder.winfo_children() if isinstance(w, SortList)]
assert sl_after, '切完主力后候补 holder 里没有 SortList (这就是 bug)'
assert len(sl_after[0].rows) >= 4, f'切完候补数异常: {len(sl_after[0].rows)}'

# 再切一次到本地, 触发 sync_local 路径
primary_box._pick('local:sensevoice')
root.update_idletasks()
sl_after2 = [w for w in holder.winfo_children() if isinstance(w, SortList)]
assert sl_after2 and len(sl_after2[0].rows) >= 4

root.destroy()
print('OK')