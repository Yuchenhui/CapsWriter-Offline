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
SAMPLE_FILE_NAME = 'voice_samples.jsonl'
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


def record_sample(base_dir, *, task_id: str, audio_path, raw_text: str, polished_text: str,
                  output_text: str, engine: str, polish: str, structure: bool, gain_db: float) -> None:
    """把同一句的原始录音路径、ASR 原文、整理结果和最终上屏文本追加到 voice_samples.jsonl。
    只存本机; 音频文件本身由录音层写在年/月/assets 下。"""
    rec = {
        'ts': time.time(),
        'task_id': task_id,
        'audio': str(audio_path) if audio_path else '',
        'raw_text': raw_text,
        'polished_text': polished_text,
        'output_text': output_text,
        'engine': engine,
        'polish': polish,
        'structure': bool(structure),
        'gain_db': gain_db,
    }
    path = Path(base_dir) / SAMPLE_FILE_NAME
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def complete_capture_sample(base_dir, **sample) -> int:
    """写入一条诊断样本并把剩余采集句数减一; 返回剩余数。采集关闭时不写。"""
    from config_client import ClientConfig as Config
    remaining = max(0, int(getattr(Config, 'diagnostic_capture_remaining', 0) or 0))
    if remaining == 0:
        return 0
    record_sample(base_dir, **sample)
    remaining -= 1
    Config.diagnostic_capture_remaining = remaining
    try:
        from core.client import user_state
        user_state.save()
    except Exception as e:
        logger.warning(f'保存对照样本采集进度失败: {e}')
    return remaining
