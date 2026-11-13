"""
托盘里改过的开关持久化到安装目录 user_state.json, 重启后恢复 (覆盖 config_client.py 的默认值).
deploy-local.ps1 不拷这个文件, 部署不会冲掉用户的选择.
"""
import json
import logging
from pathlib import Path

from config_client import ClientConfig as Config

logger = logging.getLogger('client.' + __name__.rsplit('.', 1)[-1])   # 挂到 client 下才会写进 client_latest.log

STATE_FILE = Path('user_state.json')   # 工作目录即安装目录
KEYS = ('polish', 'polish_structure', 'translate_enabled', 'translate_language', 'translate_provider', 'capsule_theme', 'capsule_follow_mouse', 'asr_engine', 'asr_fallback', 'mic_auto', 'mic_priority', 'mic_idle_release_sec', 'max_record_sec', 'vad_enable', 'asr_agc', 'asr_agc_gain_db', 'pre_roll_enabled', 'pre_roll_seconds', 'diagnostic_capture_remaining', 'output_mode')
OUTPUT_MODES = ('clipboard', 'tsf')    # 上屏方式枚举; 只允许合法值落盘/加载


def load() -> None:
    """启动时调用: 把 user_state.json 里的值写回 Config."""
    try:
        data = json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return
    except Exception as e:
        logger.warning(f'读取 {STATE_FILE} 失败, 用默认配置: {e}')
        return
    for k in KEYS:
        if k in data:
            setattr(Config, k, data[k])
    if getattr(Config, 'output_mode', None) not in OUTPUT_MODES:
        Config.output_mode = 'clipboard'   # 手改/旧文件里的非法值 -> 回默认
    # 旧版 polish 是 True/False, 换成服务商 id
    from core.tools.polish_providers import resolve
    Config.polish = resolve(Config.polish)


def set_output_mode(mode: str) -> bool:
    """上屏方式持久化入口 (设置页等共用): 枚举校验, 非法值不落盘。返回是否成功"""
    if mode not in OUTPUT_MODES:
        logger.warning(f'拒绝非法上屏方式: {mode!r}')
        return False
    previous = getattr(Config, 'output_mode', 'clipboard')
    Config.output_mode = mode
    try:
        _write_state()
    except Exception as e:
        Config.output_mode = previous
        logger.warning(f'保存上屏方式失败: {e}')
        return False
    logger.info(f'设置: 上屏方式 -> {mode}')
    return True


def _write_state() -> None:
    STATE_FILE.write_text(json.dumps({k: getattr(Config, k) for k in KEYS}, ensure_ascii=False, indent=2),
                          encoding='utf-8')


def save() -> None:
    """托盘改了开关后调用."""
    try:
        _write_state()
    except Exception as e:
        logger.warning(f'保存 {STATE_FILE} 失败: {e}')
