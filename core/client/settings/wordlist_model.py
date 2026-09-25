# coding: utf-8
"""
词库数据 (本地改 2026-09-25): 设置窗口按"一行一条"显示与增删, 文件只存纯数据 (注释由界面说明取代).
格式与各读取方保持一致, 读取代码无需改:
  terms.txt / hot.txt: 一行一条 (# 开头与空行忽略)            -> core/tools/terms.py, core/client/hotword/hot_phoneme.py
  hot-rule.txt: 一行一条 "原来的 = 换成的" (两侧空格, 右侧可空) -> core/client/hotword/hot_rule.py (按 ' = ' 切分)
"""
import re


def parse_items(text: str) -> list:
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith('#')]


def format_items(items: list) -> str:
    return '\n'.join(items) + ('\n' if items else '')


def parse_rules(text: str) -> list:
    """[(原来的, 换成的)]; 兼容旧文件里 '原来的   =' 这种右侧为空、没有尾随空格的写法 (hot_rule 原本会跳过它)"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith('#'):
            continue
        if ' = ' in s:
            pat, rep = s.split(' = ', 1)
        elif s.endswith(' ='):
            pat, rep = s[:-2], ''
        else:
            continue
        out.append((pat.strip(), rep.strip()))
    return out


def format_rules(rules: list) -> str:
    """写成 hot_rule 能读的格式: 两侧留空格, 右侧为空时也保留 ' = ' 后的空格"""
    return ''.join(f'{p} = {r}\n' for p, r in rules)


def rule_error(pat: str, rep: str):
    """新规则有问题返回原因 (中文), 没问题返回 None"""
    if not pat.strip():
        return '"原来的"不能为空'
    if ' = ' in pat or ' = ' in rep:
        return '不能包含"空格=空格"（文件里用它分隔左右两边）'
    try:
        re.compile(pat)
    except re.error as e:
        return f'正则写错了: {e}'
    return None


if __name__ == '__main__':
    old = '# 说明\n\n毫安时     =      mAh\n\\/sil   =\n\\[breath\\]  =    \n坏行没有等号\n欧拉[玛码]   =  Ollama\n'
    rules = parse_rules(old)
    assert rules == [('毫安时', 'mAh'), ('\\/sil', ''), ('\\[breath\\]', ''), ('欧拉[玛码]', 'Ollama')], rules
    text = format_rules(rules)
    assert parse_rules(text) == rules, '写出再读回一致'
    for line in text.splitlines():                      # hot_rule.update_rules 的切法: split(' = ') 恰好两段
        assert len(line.split(' = ')) == 2, line
    assert parse_items('# c\nWSL\n\n  Debian \n') == ['WSL', 'Debian']
    assert format_items(['a', 'b']) == 'a\nb\n' and format_items([]) == ''
    assert rule_error('毫安时', 'mAh') is None
    assert rule_error('((', 'x').startswith('正则写错了')
    assert rule_error('', 'x') and rule_error('a = b', 'c')
    print('wordlist_model selftest ok')
