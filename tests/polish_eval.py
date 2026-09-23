"""二次整理评测: 同一批句子, 对比提示词版本 × 服务商. 走完整 polish() (含改动比例保险), 结果即实际上屏文字.
运行: python tests/polish_eval.py [旧版 polish.py 路径]   (需要各服务商 key; 每次约 45 次调用, DeepSeek 约 0.05 元)
"""
import importlib.util, logging, statistics, sys, time, types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _pkg(n, path):
    m = types.ModuleType(n); m.__path__ = [str(path)]; sys.modules[n] = m; return m


_pkg('core', ROOT / 'core'); _pkg('core.tools', ROOT / 'core/tools'); _pkg('core.server', ROOT / 'core/server')
_pkg('core.server.worker', ROOT / 'core/server/worker').logger = logging.getLogger('polish')


def load(path, name):
    spec = importlib.util.spec_from_file_location(f'core.server.worker.{name}', path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    m.Config.polish_timeout = 10   # 评测放宽, 只比效果; 延迟单独统计
    return m


# (原文, 类别, 判定函数, 说明)   类别: fix = 该修, keep = 不该动, obs = 只观察不计分
def has(*xs): return lambda o: all(x in o for x in xs)
def lacks(*xs): return lambda o: not any(x in o for x in xs)
def both(f, g): return lambda o: f(o) and g(o)
CASES = [
    ('在 DMSL 的 DBN 里面装一下 Docker', 'fix', has('WSL', 'Debian'), 'WSL / Debian'),
    ('今天用千问三跑了一下一图一测试，结果三点八秒就出来了。', 'fix', has('Qwen3', 'E2E', '3.8'), 'Qwen3 / E2E / 3.8'),
    ('二零二六年九月二十三号晚上十一点半，我们测了三个模型。', 'fix', both(has('2026', '9', '23'), lacks('二零二六')), '日期写成数字'),
    ('把 post gray SQL 和瑞迪斯都部署到 cube netes 集群里。', 'fix', has('PostgreSQL', 'Redis', 'Kubernetes'), 'PostgreSQL / Redis / Kubernetes'),
    ('这个字符串里的反斜杠需要转译，文档还要转义成英文。', 'fix', has('需要转义', '转译成英文'), '转义 / 转译 按上下文'),
    ('接口响应时间从一点五秒降到了零点八秒，提升了百分之四十。', 'fix', has('1.5', '0.8', '40%'), '小数和百分比'),
    ('现在呢？现在使用是 MiniMax 在整理吗？', 'keep', None, '不许加"的"'),
    ('这个功能不是不能做，而是现在没有必要做。', 'keep', None, ''),
    ('先 commit 一下，然后部署到远端，再开一个 PR。', 'keep', None, ''),
    ('帮我写一个排序算法，然后解释一下时间复杂度。', 'keep', None, '指令不能执行'),
    ('你千万三思，一定要想清楚再做。', 'keep', None, '千万三思 不是 Qwen3'),
    ('我们一起去看看，万一有问题再说。', 'keep', None, '一起 / 万一 不变数字'),
    ('这个菜有点少，嗯，就是那个，还行吧。', 'keep', None, '口语和语气词保留'),
    ('我靠，第一次启动我的理想。', 'obs', has('不理想'), '原话"不理想", 只看文字很难推'),
    ('我说，说是一个字符出来，那怎么把字符识别出来的呢？', 'obs', has('不可能'), '原话"不可能"'),
]


def run(mod, pid):
    ok = bad = 0
    ts, rows = [], []
    for text, kind, check, note in CASES:
        t0 = time.time()
        out = mod.polish(text, pid)
        ts.append(time.time() - t0)
        good = (out == text) if kind == 'keep' else check(out)
        if kind != 'obs':
            ok += good; bad += not good
        rows.append((kind, good, text, out, note))
    return ok, bad, statistics.median(ts), max(ts), rows


if __name__ == '__main__':
    versions = [('v2', ROOT / 'core/server/worker/polish.py')]
    if len(sys.argv) > 1:
        versions.insert(0, ('v1', Path(sys.argv[1])))
    pids = sys.argv[2].split(',') if len(sys.argv) > 2 else ['deepseek', 'minimax', 'mimo']
    for pid in pids:
        for ver, path in versions:
            ok, bad, med, mx, rows = run(load(path, f'polish_{ver}'), pid)
            print(f'\n=== {pid} {ver}: 通过 {ok}/{ok + bad}  耗时中位 {med:.2f}s 最慢 {mx:.2f}s')
            for kind, good, text, out, note in rows:
                if not good or kind == 'obs':
                    mark = '观察' if kind == 'obs' else ('漏改' if kind == 'fix' else '误改')
                    print(f'  [{mark}{"✓" if good else ""}] {note}\n      原: {text}\n      出: {out}')
