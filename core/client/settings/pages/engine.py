# coding: utf-8
import tkinter as tk

from core.client.settings.widgets import Button, Card, ChoiceGroup, ToggleRow, font, heading, section

TITLE = '识别引擎'
# 单价与速度 (2026-09-25 实测 / 官方价): 见 core/tools/asr_usage.PRICES
# 只写价格 + 评分. 识别: pc-tweaks windows/capswriter/asr-benchmark.md (12 个关键词, 低/中/高三档平均);
# 整理: tests/polish_eval.py 38 题; 除 Step 5 (2026-09-29) 外均为 2026-09-26 实测
CLOUD_DETAIL = {'qwen-stream': '约 ¥0.4/小时 · 10.3/12',
                'doubao-stream': '¥1/小时 · 7.7/12',
                'doubao-batch': '¥1/小时 · 9.0/12',
                'qwen-batch': '¥0.79/小时 · 10.7/12',
                'zhipu-batch': '¥3.6/小时 · 10.0/12',
                'mimo-batch': '套餐内 · 10.0/12',
                'step-batch': '按量 ¥0.15/小时 · 10.5/12',
                'step-plan-batch': '套餐内 · 10.5/12',
                'minimax-batch': '套餐内 · 10.0/12'}
LOCAL_SCORE = {'qwen_asr': '免费 · 9.0/12', 'fun_asr_nano': '免费 · 5.7/12', 'sensevoice': '免费 · 5.0/12'}
POLISH = (('', '关', ''), ('deepseek', 'DeepSeek V4 Flash', '按量 · 37/38'),
          ('minimax', 'MiniMax M3', '套餐内 · 35/38'), ('mimo', 'MiMo V2.6 Flash', '套餐内 · 33/38'),
          ('kimi', 'Kimi K3', '套餐内 · 36/38'), ('zhipu', 'GLM-5.3 Flash', '套餐内 · 35/38'),
          ('longcat', 'LongCat 2.5', '套餐内 · 34/38'),
          ('step', 'Step 5 Preview', '套餐内 · 34/38'))


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
    polish_items = [{'key': k, 'title': t, 'detail': d} for k, t, d in POLISH]

    def pick_polish(k):
        ctx.do('set_polish', k)
    ChoiceGroup(f, pal, polish_items, st.get('polish', ''), pick_polish).pack(fill='x')
    ToggleRow(f, pal, '结构化整理', '说多件事时编号、分行', bool(st.get('polish_structure')),
              lambda v: ctx.do('set_structure', v)).pack(fill='x', pady=(12, 0))

    ToggleRow(f, pal, '语音活动检测 (VAD)', '过滤敲桌/键盘等语音噪声', bool(st.get('vad_enable', True)),
              lambda v: ctx.do('set_vad', v)).pack(fill='x', pady=(22, 0))

    section(f, pal, '连通性测试').pack(anchor='w', pady=(26, 8))
    _probe_section(f, pal)
    return f


def _probe_section(f, pal):
    """连通性测试 (2026-10-12): 每个云端引擎一行, 按钮向每家发一次极短的真实请求, 行右侧就地显示结果.
    订阅制引擎 (如阶跃 Step Plan) 没有余额接口, 只有真发请求才知道额度是否耗尽, 这正是本功能的目的."""
    import queue
    import threading
    from core.client.audio import cloud_asr
    box = Card(f, pal)
    box.pack(fill='x')
    body = box.body
    tk.Label(body, text='向每家云端发一次极短的真实请求（约 0.5 秒音频）。订阅制引擎（如阶跃 Step Plan）没有余额接口，'
                        '只能这样测出额度是否耗尽；按量引擎测一次约 0.00002 元。', font=font(9), bg=pal.surface,
             fg=pal.muted, wraplength=560, justify='left').pack(anchor='w', pady=(0, 10))
    rows = {}
    for key, (name, kind, _, _) in cloud_asr.ENGINES.items():
        if kind == 'local':
            continue
        row = tk.Frame(body, bg=pal.surface)
        row.pack(fill='x', pady=2)
        tk.Label(row, text=name, font=font(10), bg=pal.surface, fg=pal.fg, anchor='w').pack(side='left')
        lab = tk.Label(row, text='未测试', font=font(10), bg=pal.surface, fg=pal.muted)
        lab.pack(side='right')
        rows[key] = lab

    results = queue.Queue()          # 测试在后台线程跑, 结果经队列回到 Tk 线程刷新 (跨线程不直接碰控件)

    def paint():
        try:
            while True:
                key, res = results.get_nowait()
                lab = rows.get(key)
                if lab is None or not lab.winfo_exists():
                    continue
                if res['ok']:
                    lab.configure(text=f'✓ 正常 · {res["ms"]}ms', fg=pal.accent)
                else:
                    what = cloud_asr._HINTS.get(res['kind'], cloud_asr._HINTS['error'])[0]
                    extra = f'（{res["detail"]}）' if res['kind'] == 'error' and res['detail'] else ''
                    lab.configure(text=f'✗ {what}{extra}', fg=pal.danger)
        except queue.Empty:
            pass
        if f.winfo_exists():
            f.after(120, paint)

    def one(key):
        try:
            res = cloud_asr.probe(key)
        except Exception as e:                       # probe 自己兜异常, 这层只为保险
            res = {'ok': False, 'kind': 'error', 'detail': f'{type(e).__name__}: {e}'[:80], 'ms': 0}
        results.put((key, res))

    def test():
        for lab in rows.values():
            lab.configure(text='测试中…', fg=pal.muted)
        for key in rows:                             # 每家一个线程 (打的是不同主机), 出结果各自刷新
            threading.Thread(target=one, args=(key,), daemon=True, name=f'asr-probe-{key}').start()

    Button(body, pal, '测试全部', test, primary=True).pack(anchor='w', pady=(10, 0))
    f.after(120, paint)
