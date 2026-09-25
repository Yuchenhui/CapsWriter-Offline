# coding: utf-8
import tkinter as tk

from core.client.settings.widgets import ChoiceGroup, Segmented, Toggle, heading, section, font

TITLE = '识别引擎'
# 单价与速度 (2026-09-25 实测 / 官方价): 见 core/tools/asr_usage.PRICES
CLOUD_DETAIL = {'qwen-stream': '约 ¥0.4/小时 · 松开后 0.2 秒 · 边说边出字',
                'doubao-stream': '¥1/小时（有试用额度）· 松开后 0.9 秒',
                'doubao-batch': '¥1/小时（与流式共用额度）· 1 秒 · 比豆包流式准',
                'qwen-batch': '¥0.79/小时 · 0.5 秒',
                'zhipu-batch': '¥3.6/小时 · 0.5 秒',
                'mimo-batch': '套餐内 · 1.2 秒',
                'minimax-batch': '套餐内 · 1.5–2 秒 · 声音小会识别为空'}
POLISH = (('', '关', '松开后约 0.4 秒出字'), ('deepseek', 'DeepSeek V4 Flash', '约 +0.6 秒'),
          ('minimax', 'MiniMax M3', '约 +1.1 秒'), ('mimo', 'MiMo V2.6 Flash', '约 +0.7 秒，偶尔超时'),
          ('kimi', 'Kimi K3', '约 +1.5 秒，评测 36/38'), ('zhipu', 'GLM-5.3 Flash', '约 +1.3 秒，评测 35/38'))


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
            items = [{'key': k, 'title': name, 'detail': '已安装' if ok else '未安装', 'disabled': not ok}
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
            local = dict((k, n) for k, n, _ in ctx.local_models()).get(ctx.local_model(), '')
            tk.Label(body, text=f'云端失败时用本地「{local}」兜底；一直用云端时本地模型闲置 60 秒后自动释放显存',
                     font=font(9), bg=pal.bg, fg=pal.muted).pack(anchor='w', pady=(6, 0))

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
