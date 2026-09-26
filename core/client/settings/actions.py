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
    try:                                   # 托盘状态行跟着变
        from core.ui.tray import refresh_menu
        refresh_menu()
    except Exception:
        pass
    return True


def set_theme(key: str) -> bool:
    return key in THEMES and _set('capsule_theme', key, '胶囊主题')


def set_engine(key: str) -> bool:
    from core.client.audio.cloud_asr import ENGINES
    return key in ENGINES and _set('asr_engine', key, '识别引擎')


def set_fallback(order: list) -> bool:
    """候补顺序 (设置窗口拖拽排序): 非流式云端 key / 'local:<model_type>'"""
    from core.client.audio.cloud_asr import ENGINES
    order = [k for k in order if k in ENGINES or k.startswith('local:')]
    return _set('asr_fallback', order, '候补顺序')


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


def set_mic_auto(on: bool) -> bool:
    return _set('mic_auto', bool(on), '麦克风自动切换')


def set_mic_priority(order: list) -> bool:
    return _set('mic_priority', [str(n) for n in order], '麦克风优先顺序')


IDLE_RELEASE_SEC = 1   # 说完 1 秒就关 (像微信输入法); 1 秒内接着按不关, 省一次重开


def set_mic_idle(app, on: bool) -> bool:
    """开: 说完 1 秒关麦克风, 按右 Alt 时后台重开 (idle_release); 关: 麦克风常开, 已关着就立刻打开"""
    _set('mic_idle_release_sec', IDLE_RELEASE_SEC if on else 0, '麦克风闲置释放 (秒)')
    if app is None:
        return True
    from core.client.audio import idle_release
    if on:
        idle_release.schedule(app)
    elif not app.stream._running:
        import threading
        threading.Thread(target=app.stream.start, daemon=True, name='mic-wake').start()
    return True


STARTUP_LNK = Path(os.environ.get('APPDATA', '')) / 'Microsoft/Windows/Start Menu/Programs/Startup/CapsWriter.lnk'


def autostart_on() -> bool:
    return STARTUP_LNK.exists()


def set_autostart(base, on: bool) -> bool:
    """开机自启 = Startup 里的 CapsWriter.lnk, 同桌面 / 开始菜单快捷方式: wscript start-hidden.vbs"""
    if not on:
        STARTUP_LNK.unlink(missing_ok=True)
        logger.info('设置: 开机自启 -> 关')
        return True
    import subprocess
    base = Path(base).resolve()
    env = {**os.environ, 'CW_LNK': str(STARTUP_LNK), 'CW_BASE': str(base),   # 路径走环境变量, 命令里不写反斜杠
           'CW_TARGET': str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'System32' / 'wscript.exe'),
           'CW_ARGS': f'"{base / "start-hidden.vbs"}"', 'CW_ICON': f'{base / "assets" / "icon.ico"},0'}
    ps = ('$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:CW_LNK); $s.TargetPath = $env:CW_TARGET; '
          '$s.Arguments = $env:CW_ARGS; $s.WorkingDirectory = $env:CW_BASE; $s.IconLocation = $env:CW_ICON; $s.Save()')
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps], env=env,
                           capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        logger.warning(f'建开机自启快捷方式失败: {e}')
        return False
    ok = r.returncode == 0 and STARTUP_LNK.exists()
    logger.info(f'设置: 开机自启 -> 开 ({"成功" if ok else "失败: " + r.stderr.strip()[:200]})')
    return ok


MAX_RECORD_RANGE = (10, 180)


def set_max_record(sec) -> bool:
    lo, hi = MAX_RECORD_RANGE
    return _set('max_record_sec', int(min(max(int(sec), lo), hi)), '单次录音上限 (秒)')


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
