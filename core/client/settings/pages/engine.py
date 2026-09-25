# coding: utf-8
import tkinter as tk

from core.client.settings.widgets import ChoiceGroup, Segmented, Toggle, heading, section, font

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


def build(parent, pal, ctx):
    from core.client.audio import cloud_asr
    f = tk.Frame(parent, bg=pal.bg)
    heading(f, pal, '识别引擎', '说完话由谁把声音变成文字').pack(anchor='w', pady=(0, 16))
    st = ctx.state()
    eng = cloud_asr._ALIASES.get(st['asr_engine'], st['asr_engine'])
    mode = 'local' if eng == 'local' else 'cloud'
    last_cloud = [eng if eng != 'local' else 'qwen-stream']   # 从本地切回云端时恢复上次的云端引擎
    body = tk.Frame(f, bg=pal.bg)

    def render(m):
        for w in body.winfo_children():
            w.destroy()
        if m == 'local':
            models = ctx.local_models()
            items = [{'key': k, 'title': name, 'detail': LOCAL_SCORE.get(k, '') if ok else '未安装', 'disabled': not ok}
                     for k, name, ok in models]
            section(body, pal, '本地模型（免费，不联网）').pack(anchor='w', pady=(0, 8))
            ChoiceGroup(body, pal, items, ctx.local_model(), lambda k: ctx.do('set_local_model', k)).pack(fill='x')
            tk.Label(body, text='切换本地模型会重启识别服务，约 5–15 秒', font=font(9), bg=pal.bg, fg=pal.muted).pack(anchor='w', pady=(6, 0))
        else:
            groups = []                        # 流式 / 非流式两组共用一个选中状态: 选中一组里的, 另一组取消

            def pick(k):
                for g in groups:
                    g.select(k)
                last_cloud[0] = k
                ctx.do('set_engine', k)
            for kind, label in (('stream', '流式 · 边说边出字'), ('batch', '非流式 · 松开后识别')):
                items = []
                for k, (name, knd, _, env) in cloud_asr.ENGINES.items():
                    if knd == kind:
                        items.append({'key': k, 'title': name, 'detail': CLOUD_DETAIL.get(k, ''),
                                      'warn': '' if cloud_asr._has_key(env) else f'缺 {env}'})
                section(body, pal, label).pack(anchor='w', pady=(0 if kind == 'stream' else 16, 8))
                g = ChoiceGroup(body, pal, items, last_cloud[0], pick)
                g.pack(fill='x')
                groups.append(g)

    def switch(m):                        # 本地 / 云端 就是总开关: 切过去立即生效
        ctx.do('set_engine', 'local' if m == 'local' else last_cloud[0])
        render(m)

    Segmented(f, pal, (('local', '本地模型'), ('cloud', '云端模型')), mode, switch).pack(anchor='w')
    body.pack(fill='x', pady=(16, 0))
    render(mode)

    section(f, pal, '识别后整理').pack(anchor='w', pady=(26, 8))
    items = [{'key': k, 'title': t, 'detail': d} for k, t, d in POLISH]
    ChoiceGroup(f, pal, items, st.get('polish', ''), lambda k: ctx.do('set_polish', k)).pack(fill='x')
    row = tk.Frame(f, bg=pal.bg)
    row.pack(fill='x', pady=(12, 0))
    tk.Label(row, text='结构化整理：说多件事时编号、分行', font=font(11), bg=pal.bg, fg=pal.fg).pack(side='left')
    Toggle(row, pal, bool(st.get('polish_structure')), lambda v: ctx.do('set_structure', v), bg=pal.bg).pack(side='right')
    return f
