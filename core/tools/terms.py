"""
术语表: 安装目录下 terms.txt, 一行一个词, # 开头为注释.
客户端拿去当 Qwen3-ASR 的识别 context, 服务端拿去给二次整理; 按修改时间缓存, 改完保存即生效.
"""
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
