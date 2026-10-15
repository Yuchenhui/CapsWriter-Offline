# coding: utf-8
"""
输出历史 (本地新增 2026-10-15)

每句最终上屏的文字 (直输 / LLM 透传 / LLM 润色 / toast 结果) 追加一行 JSONL 到
根目录 history.jsonl; 设置窗口「历史」页列出并逐句复制 —— 话识别完了但没打到
目标位置时, 从这里找回。

只追加、读取, 失败静默降级 (记日志), 绝不影响上屏主流程。
"""
import json
import time
from pathlib import Path

from core.client import logger

FILE_NAME = 'history.jsonl'
MAX_BYTES = 512 * 1024     # 超过就裁剪
KEEP_LINES = 300           # 裁剪后保留的最近条数


def record(base_dir, text: str) -> None:
    """追加一条输出历史; 空文本不记"""
    text = (text or '').strip()
    if not text:
        return
    path = Path(base_dir) / FILE_NAME
    line = json.dumps({'ts': time.time(), 'text': text}, ensure_ascii=False)
    with path.open('a', encoding='utf-8') as f:
        f.write(line + '\n')
    if path.stat().st_size > MAX_BYTES:
        lines = path.read_text(encoding='utf-8').splitlines()[-KEEP_LINES:]
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        logger.info(f'输出历史超出 {MAX_BYTES // 1024}KB, 已裁剪到最近 {KEEP_LINES} 条')


def read_entries(base_dir, limit: int = 200) -> list:
    """读取历史, 最新在前: [{'ts': float, 'text': str}]; 坏行跳过"""
    try:
        lines = (Path(base_dir) / FILE_NAME).read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.warning(f'读取输出历史失败: {e}')
        return []
    entries = []
    for ln in reversed(lines):
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        t = e.get('text')
        if isinstance(t, str) and t.strip():
            entries.append({'ts': e.get('ts', 0), 'text': t})
        if len(entries) >= limit:
            break
    return entries
