# coding: utf-8
import tkinter as tk

from core.client.settings.widgets import ChoiceGroup, Toggle, heading, section, font

TITLE = '识别引擎'
# 单价与速度 (2026-09-25 实测 / 官方价): 见 core/tools/asr_usage.PRICES
# 只写价格 + 评分. 识别: pc-tweaks windows/capswriter/asr-benchmark.md (12 个关键词, 低/中/高三档平均);
# 整理: tests/polish_eval.py 38 题. 均为 2026-09-26 实测
CLOUD_DETAIL = {'qwen-stream': '约 ¥0.4/小时 · 10.3/12',
                'doubao-stream': '¥1/小时 · 7.7/12',
                'doubao-batch': '¥1/小时 · 9.0/12',
                'qwen-batch': '¥0.79/小时 · 10.7/12',
                'zhipu-batch': '¥3.6/小时 · 10.0/12',
                'mimo-batch': '套餐内 · 10.0/12',
                'step-batch': '¥0.15/小时 · 10.5/12',
                'minimax-batch': '套餐内 · 10.0/12'}
LOCAL_SCORE = {'qwen_asr': '免费 · 9.0/12', 'fun_asr_nano': '免费 · 5.7/12', 'sensevoice': '免费 · 5.0/12'}
POLISH = (('', '关', ''), ('deepseek', 'DeepSeek V4 Flash', '按量 · 37/38'),
          ('minimax', 'MiniMax M3', '套餐内 · 35/38'), ('mimo', 'MiMo V2.6 Flash', '套餐内 · 33/38'),
          ('kimi', 'Kimi K3', '套餐内 · 36/38'), ('zhipu', 'GLM-5.3 Flash', '套餐内 · 35/38'))


def _all(ctx):
    """[{key, title, detail, group, warn, kind}]: 流式 / 非流式云端, 已安装的本地模型 (key = local:<model_type>)"""
    from core.client.audio import cloud_asr
    items = []
    for kind, group in (('stream', '流式'), ('batch', '非流式')):
        for k, (name, knd, _, env) in cloud_asr.ENGINES.items():
            if knd == kind:
                items.append({'key': k, 'title': name, 'detail': CLOUD_DETAIL.get(k, ''), 'group': group, 'kind': kind,
                              'warn': '' if cloud_asr._has_key(env) else f'缺 {env}'})
    for k, name, ok in ctx.local_models():
        if ok:
            items.append({'key': f'local:{k}', 'title': f'本地 {name}', 'detail': LOCAL_SCORE.get(k, ''), 'group': '本地',
                          'kind': 'local', 'local': True})
    return items


def _score(item) -> float:
    try:
        return float(item.get('detail', '').rsplit(' · ', 1)[-1].split('/')[0])
    except ValueError:
        return 0.0


def _fallback_items(all_items, primary, saved, loaded=''):
    """候补列表: 非流式云端 + 本地, 去掉主力. 按保存的顺序; 没排过的补在后面:
    云端按评分高到低, 本地里当前加载的排第一 (否则一拖动就会把常驻本地模型切成排在前面的那个)"""
    pool = [i for i in all_items if i['kind'] != 'stream' and i['key'] != primary]
    rank = {k: n for n, k in enumerate(saved)}
    return sorted(pool, key=lambda i: (rank.get(i['key'], len(rank)), i['kind'] == 'local',
                                       i['key'] != f'local:{loaded}', -_score(i)))


def build(parent, pal, ctx):
    from core.client.settings.widgets import Dropdown, SortList
    f = tk.Frame(parent, bg=pal.bg)
    heading(f, pal, '识别引擎', '说完话由谁把声音变成文字').pack(anchor='w', pady=(0, 16))
    st = ctx.state()
    items = _all(ctx)
    primary = st['asr_engine'] if st['asr_engine'] != 'local' else f'local:{ctx.local_model()}'
    saved = list(st.get('asr_fallback') or [])
    holder = tk.Frame(f, bg=pal.bg)

    def sync_local(order, prim):
        """常驻加载的本地模型 = 主力 (若是本地) 或候补里排最前的本地"""
        loc = prim if prim.startswith('local:') else next((k for k in order if k.startswith('local:')), '')
        if loc and loc[6:] != ctx.local_model():
            ctx.do('set_local_model', loc[6:])

    def save_order(order):
        ctx.do('set_fallback', order)
        sync_local(order, primary_box.key)

    def render_list():
        for w in holder.winfo_children():
            w.destroy()
        fb = _fallback_items(items, primary_box.key, saved, ctx.local_model())
        SortList(holder, pal, fb, lambda order: (saved.__setitem__(slice(None), order), save_order(order))).pack(fill='x')

    def pick(key):
        ctx.do('set_engine', 'local' if key.startswith('local:') else key)
        render_list()
        sync_local(saved, key)

    section(f, pal, '主力').pack(anchor='w', pady=(0, 8))
    primary_box = Dropdown(f, pal, items, primary, pick)
    primary_box.pack(fill='x')
    section(f, pal, '候补（拖动排序）').pack(anchor='w', pady=(18, 8))
    holder.pack(fill='x')
    render_list()

    section(f, pal, '识别后整理').pack(anchor='w', pady=(26, 8))
    items = [{'key': k, 'title': t, 'detail': d} for k, t, d in POLISH]
    ChoiceGroup(f, pal, items, st.get('polish', ''), lambda k: ctx.do('set_polish', k)).pack(fill='x')
    row = tk.Frame(f, bg=pal.bg)
    row.pack(fill='x', pady=(12, 0))
    tk.Label(row, text='结构化整理：说多件事时编号、分行', font=font(11), bg=pal.bg, fg=pal.fg).pack(side='left')
    Toggle(row, pal, bool(st.get('polish_structure')), lambda v: ctx.do('set_structure', v), bg=pal.bg).pack(side='right')
    return f
