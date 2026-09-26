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
KEYS = ('polish', 'polish_structure', 'capsule_theme', 'asr_engine', 'asr_fallback', 'mic_auto', 'mic_priority')                     # 允许持久化的 Config 属性


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
    # 旧版 polish 是 True/False, 换成服务商 id
    from core.tools.polish_providers import resolve
    Config.polish = resolve(Config.polish)


def save() -> None:
    """托盘改了开关后调用."""
    try:
        STATE_FILE.write_text(json.dumps({k: getattr(Config, k) for k in KEYS}, ensure_ascii=False, indent=2),
                              encoding='utf-8')
    except Exception as e:
        logger.warning(f'保存 {STATE_FILE} 失败: {e}')
