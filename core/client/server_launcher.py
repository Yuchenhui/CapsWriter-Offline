"""
客户端托管服务端: 只启动客户端即可, 服务端作为隐藏子进程由这里拉起 (一个程序 / 一个托盘图标).

守护线程每 5 秒看一次服务端端口; 没人监听就隐藏启动 start_server.exe.
顺带覆盖: 服务端崩溃自动拉起; 托盘「重启」客户端时旧客户端关掉服务端后, 新客户端自己补起来.
退出时只关自己拉起的那个服务端 (连同其识别子进程), 不碰外部手动启动的.
"""
import logging
import os
import re
import socket
import subprocess
import sys
import threading
from pathlib import Path

from config_client import ClientConfig as Config

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000
_CHECK_INTERVAL = 5

_proc = None
_stop = threading.Event()


def _server_listening() -> bool:
    try:
        socket.create_connection((Config.addr, int(Config.port)), timeout=0.5).close()
        return True
    except OSError:
        return False


def _spawn(base_dir: Path) -> None:
    global _proc
    exe = base_dir / 'start_server.exe'
    cmd = [str(exe)] if exe.exists() else [sys.executable, str(base_dir / 'start_server.py')]
    _proc = subprocess.Popen(cmd, cwd=str(base_dir), creationflags=CREATE_NO_WINDOW)
    logger.info(f'已拉起服务端 (隐藏) pid={_proc.pid}: {cmd[0]}')


def _watch(base_dir: Path) -> None:
    while not _stop.is_set():
        # 端口没人听, 且不是"刚拉起还在加载模型" -> 拉起
        if not _server_listening() and (_proc is None or _proc.poll() is not None):
            try:
                _spawn(base_dir)
            except Exception as e:
                logger.error(f'拉起服务端失败: {e}')
        _stop.wait(_CHECK_INTERVAL)


def start(base_dir) -> None:
    if not getattr(Config, 'auto_start_server', False):
        return
    threading.Thread(target=_watch, args=(Path(base_dir),), daemon=True, name='server-launcher').start()


def stop() -> None:
    _stop.set()
    if _proc is not None and _proc.poll() is None:
        # /T 连同识别子进程一起结束
        subprocess.run(['taskkill', '/PID', str(_proc.pid), '/T', '/F'],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
        logger.info(f'已关闭托管的服务端 pid={_proc.pid}')


# ---- 托盘切换识别模型 ------------------------------------------------------
MODELS = {  # model_type -> (菜单名, 判定模型已安装的目录)
    'sensevoice': ('SenseVoice (快)', 'models/SenseVoice-Small/Sensevoice-Small-ONNX'),
    'qwen_asr': ('Qwen3-ASR 1.7B (准)', 'models/Qwen3-ASR/Qwen3-ASR-1.7B'),
}
_MODEL_RE = re.compile(r"^(\s*model_type\s*=\s*)'([^']*)'", re.M)


def current_model(base_dir) -> str:
    m = _MODEL_RE.search((Path(base_dir) / 'config_server.py').read_text(encoding='utf-8'))
    return m.group(2) if m else ''


def switch_model(base_dir, model_type: str) -> bool:
    """改写 config_server.py 的 model_type 并重启托管的服务端 (守护线程几秒内拉起新的)."""
    base_dir = Path(base_dir)
    if model_type == current_model(base_dir):
        return True
    if not (base_dir / MODELS[model_type][1]).is_dir():
        logger.warning(f'模型未安装, 不切换: {MODELS[model_type][1]}')
        return False
    cfg = base_dir / 'config_server.py'
    tmp = cfg.with_suffix('.py.tmp')   # 先写临时文件再原子替换, 写到一半崩溃不会留下坏配置
    tmp.write_text(_MODEL_RE.sub(lambda m: f"{m.group(1)}'{model_type}'", cfg.read_text(encoding='utf-8'), count=1),
                   encoding='utf-8')
    os.replace(tmp, cfg)
    if _proc is not None and _proc.poll() is None:
        subprocess.run(['taskkill', '/PID', str(_proc.pid), '/T', '/F'],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    logger.info(f'已切换识别模型为 {model_type}, 服务端重启中')
    return True
