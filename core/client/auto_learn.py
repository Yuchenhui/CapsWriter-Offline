"""
自动学习纠错词 · 第一步: 只收集候选, 不改任何东西 (思路来自 VoiceInk 的 AutoLearn).

粘贴后盯住焦点输入框 (UI Automation 能直接读值的那类: 浏览器输入框 / 聊天软件 / 普通文本框;
终端、VS Code 编辑区读不到值, 跳过). 每秒读一次, 最多 120 秒; 焦点离开或下一句开始时收尾:
对比"刚粘贴时"与"最后一次"的内容, 落在粘贴文字范围内的短替换 (错写 -> 你改的写法) 记入安装目录 learn_candidates.jsonl.
以后再加: 大模型审核候选 -> 托盘里让用户确认 -> 写进术语表 / 替换规则.
"""
import difflib
import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

WATCH_SEC = 120
POLL_SEC = 1.0
MAX_TEXT = 20000          # 输入框内容超过这个长度就不看 (大文档 diff 慢, 也不像是在改一句话)
_gen = 0                  # 每次粘贴 +1; 旧的盯守线程发现自己过期就收尾
_lock = threading.Lock()


def extract(pasted: str, before: str, after: str) -> list:
    """before = 刚粘贴后的输入框内容, after = 用户改完后的. 返回 [(错写, 改后)], 只取落在粘贴文字范围内的短替换"""
    k = before.rfind(pasted)
    if not pasted or k < 0 or before == after:
        return []
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag != 'replace' or i1 < k or i2 > k + len(pasted):
            continue
        old, new = before[i1:i2].strip(), after[j1:j2].strip()
        if 1 <= len(old) <= 20 and 1 <= len(new) <= 30 and '\n' not in old + new and old != new:
            out.append((old, new, before[max(k, i1 - 6):min(k + len(pasted), i2 + 6)], after[max(0, j1 - 6):j2 + 6]))
    return out


def watch(pasted: str, base_dir, window: str = '') -> None:
    """粘贴后调用 (不阻塞): 后台线程盯守输入框"""
    global _gen
    with _lock:
        _gen += 1
        me = _gen
    threading.Thread(target=_run, args=(me, pasted, Path(base_dir), window), daemon=True, name='auto-learn').start()


def _run(me: int, pasted: str, base_dir: Path, window: str) -> None:
    from core.tools.focus_probe import focused_value
    time.sleep(0.4)   # 等目标程序处理完 Ctrl+V
    first = focused_value()
    ident = lambda r: (r.get('hwnd'), r.get('ClassName'), r.get('ControlType'))
    before = first.get('Value')
    if 'error' in first or before is None or len(before) > MAX_TEXT or pasted not in before:
        return
    last = before
    t_end = time.time() + WATCH_SEC
    while time.time() < t_end and me == _gen:
        time.sleep(POLL_SEC)
        cur = focused_value()
        if 'error' in cur or ident(cur) != ident(first) or cur.get('Value') is None:
            break   # 焦点走了: 用最后一次读到的内容收尾
        last = cur['Value']
        if len(last) > MAX_TEXT:
            return
    cands = extract(pasted, before, last)
    if not cands:
        return
    rec = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'window': window, 'pasted': pasted,
           'pairs': [{'from': a, 'to': b, 'from_ctx': ca, 'to_ctx': cb} for a, b, ca, cb in cands]}
    try:
        with open(base_dir / 'learn_candidates.jsonl', 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        logger.info(f'自动学习: 记下 {len(cands)} 个纠错候选 ' + ', '.join(f'{ca}->{cb}' for _, _, ca, cb in cands))
    except OSError as e:
        logger.warning(f'写 learn_candidates.jsonl 失败: {e}')


if __name__ == '__main__':   # 自检: 纯函数 extract
    p = '你先别急，应该是配伦那里的路径写错了。'
    before = '聊天记录\n' + p
    got = extract(p, before, before.replace('配伦那里', '配置文件里'))
    print('配伦 ->', got)
    assert len(got) == 1 and got[0][:2] == ('伦那', '置文件') and '配伦那里' in got[0][2] and '配置文件里' in got[0][3], got
    assert extract(p, before, before) == []
    assert extract(p, before, before + '\n后面又打了一行新内容') == []                 # 只追加, 没改粘贴内容
    assert extract(p, before, before.replace('聊天记录', '别的记录')) == []            # 改的不是粘贴那段
    print('auto_learn selftest ok')
