"""按任务 ID 记录语音阶段与耗时；不保存音频、原文或凭据，不参与识别决策。"""
import json
import time
from collections import OrderedDict
from threading import Lock

from . import logger

_tasks = OrderedDict()
_lock = Lock()
_LIMIT = 128


def event(task_id, stage, **fields):
    """记录阶段；有 release 时给出松手后的耗时，缺少时保持为空。"""
    try:
        now = time.monotonic()
        with _lock:
            if stage == 'start':
                _tasks[task_id] = {'start': now}
            task = _tasks.get(task_id)
            if stage == 'release' and task is not None:
                task['release'] = now
            record = {'task_id': task_id, 'stage': stage,
                      'since_start_ms': round((now - task['start']) * 1000, 1) if task else None,
                      'since_release_ms': round((now - task['release']) * 1000, 1)
                      if task and 'release' in task else None, **fields}
            while len(_tasks) > _LIMIT:
                _tasks.popitem(last=False)
        logger.info('[voice.trace] ' + json.dumps(record, ensure_ascii=False, allow_nan=False))
    except Exception:
        # 诊断失败不改变录音、识别或输出行为。
        logger.debug('语音阶段日志写入失败', exc_info=True)
