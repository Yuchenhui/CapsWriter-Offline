# coding: utf-8
"""设置操作 (设置窗口经 Context.do 调用). 都返回 bool, 失败只记日志不抛"""
import logging
import os
from pathlib import Path

from config_client import ClientConfig as Config

logger = logging.getLogger('client.settings')
WORDLIST_FILES = ('terms.txt', 'hot.txt', 'hot-rule.txt')
THEMES = ('obsidian', 'aurora', 'pebble', 'frost', 'halo', 'dark', 'light', 'auto')


def _set(attr, value, label) -> bool:
    from core.client import user_state
    setattr(Config, attr, value)
    user_state.save()
    logger.info(f'设置: {label} -> {value!r}')
    return True


def set_theme(key: str) -> bool:
    return key in THEMES and _set('capsule_theme', key, '胶囊主题')


def set_engine(key: str) -> bool:
    from core.client.audio.cloud_asr import ENGINES
    return key in ENGINES and _set('asr_engine', key, '识别引擎')


def set_polish(pid: str) -> bool:
    from core.tools.polish_providers import PROVIDERS
    return (pid == '' or pid in PROVIDERS) and _set('polish', pid, '二次整理')


def set_structure(on: bool) -> bool:
    return _set('polish_structure', bool(on), '结构化整理')


def set_local_model(base_dir, key: str) -> bool:
    from core.client import server_launcher
    try:
        return bool(server_launcher.switch_model(base_dir, key))
    except Exception as e:
        logger.warning(f'切换本地模型失败: {e}')
        return False


def set_mic(dev_id: str) -> bool:
    from core.client.audio import mic_select
    return bool(mic_select.set_default(dev_id))


def set_gain(dev_id: str, db: float) -> bool:
    from core.client.audio import mic_select
    return bool(mic_select.set_gain(dev_id, db))


def calibrate(app) -> bool:
    from core.client import calibration
    calibration.start(app)
    return True


def save_wordlist(base_dir, fname: str, text: str) -> bool:
    """只允许三个词库文件; 临时文件 + os.replace 原子替换 (写到一半崩溃不会留半截文件)"""
    if fname not in WORDLIST_FILES:
        logger.warning(f'拒绝写词库: {fname!r} 不在白名单')
        return False
    path = Path(base_dir) / fname
    tmp = path.with_suffix('.tmp')
    try:
        tmp.write_text(text, encoding='utf-8')
        os.replace(tmp, path)
    except OSError as e:
        logger.warning(f'保存 {fname} 失败: {e}')
        return False
    logger.info(f'设置: 保存词库 {fname} ({len(text.splitlines())} 行)')
    return True
