"""
术语表: 安装目录下 terms.txt, 一行一个词, # 开头为注释.
客户端拿去当 Qwen3-ASR 的识别 context, 服务端拿去给二次整理; 按修改时间缓存, 改完保存即生效.
"""
import re
from pathlib import Path

TERMS_FILE = Path('terms.txt')   # 两个进程的工作目录都是安装目录
_cache = (None, '')


def load_terms() -> str:
    """返回逗号分隔的术语串; 文件不存在返回空串."""
    global _cache
    try:
        mtime = TERMS_FILE.stat().st_mtime
    except OSError:
        return ''
    if _cache[0] != mtime:
        lines = TERMS_FILE.read_text(encoding='utf-8').splitlines()
        _cache = (mtime, ', '.join(t for t in (l.strip() for l in lines) if t and not t.startswith('#')))
    return _cache[1]


# 本地改 2026-10-29: 词库回显检测. Qwen3-ASR 的识别 context 就是整个词库; 在残留噪声/被串入的
# 无关音频上模型会复读 context, 把整个词库当识别结果吐回来. 检出后调用方拦截输出, 只给黄色 ⚠ 胶囊.
_DUMP_MIN_TERMS = 5     # 词库条数低于此值不做判定: 太短时"整库回显"与正常提到几个词无法区分
_DUMP_HIT_RATIO = 0.8   # 输出覆盖了 80% 的词条即判回显
_STRIP_RE = re.compile(r"""[\s,，、;；:：.。!！?？·\-_/\\()（）\[\]【】「」『』《》"'…]+""")


def looks_like_dump(text: str) -> bool:
    """识别结果是否疑似把整个词库原样吐回来."""
    terms = [t for t in (load_terms() or '').split(', ') if t]
    if len(terms) < _DUMP_MIN_TERMS:
        return False
    hay = _STRIP_RE.sub('', text).lower()
    if not hay:
        return False
    hit = sum(1 for t in terms if _STRIP_RE.sub('', t).lower() in hay)
    return hit / len(terms) >= _DUMP_HIT_RATIO
