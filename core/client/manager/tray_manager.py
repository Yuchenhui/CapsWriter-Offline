# coding: utf-8
"""
托盘 (本地改 2026-09-25): 三行状态 + 「设置…」(默认项, 左键单击图标也打开) + 「退出».
所有设置都在设置窗口 (core/client/settings) 里改; 原来的各设置子菜单已移除.
"""
import datetime as dt
import os

from . import logger
from config_client import ClientConfig as Config
from core.client import server_launcher


def status_lines() -> list:
    """托盘顶部三行状态 (pystray 每次重建菜单时调用; 每句识别后 refresh_menu 触发重建)"""
    from core.client.audio import cloud_asr
    from core.client.settings import stats
    from core.tools import asr_usage as au, polish_usage as pu
    from core.tools.polish_providers import PROVIDERS
    key = cloud_asr.engine_key()
    if key == 'local':
        names = {k: n for k, (n, _) in server_launcher.MODELS.items()}
        eng = f"本地 {names.get(server_launcher.current_model(os.getcwd()), '')}".strip()
    else:
        name, kind, _, _ = cloud_asr.ENGINES[key]
        eng = name.split('（')[0] + ('（在线 · 流式）' if kind == 'stream' else '（在线）')
    pol = PROVIDERS[Config.polish]['name'] if Config.polish in PROVIDERS else '关'
    today = stats.periods(au.load(), dt.date.today(), pu.load())['今日']['yuan']
    return [f'识别：{eng}', f'二次整理：{pol}', f'今日花费：{au.yuan(today)}']


class TrayManager:
    """托盘管理器"""

    def __init__(self, app):
        self.app = app

    @property
    def state(self):
        return self.app.state

    def start(self):
        """初始化系统托盘图标"""
        if not Config.enable_tray:
            return
        try:
            from ..ui import enable_min_to_tray
        except ImportError as e:
            logger.warning(f"托盘模块导入失败，跳过托盘功能: {e}")
            return
        icon_path = os.path.join(self.app.base_dir, 'assets', 'icon.ico')
        opts = [((lambda n: (lambda _item: self._status(n)))(n), 'status') for n in range(3)]
        opts.append(('设置…', self._open_settings, 'default'))
        enable_min_to_tray('CapsWriter Client', icon_path, exit_callback=self.app.stop, more_options=opts, restart=False)
        logger.info("托盘图标已启用")

    @staticmethod
    def _status(n: int) -> str:
        try:
            return status_lines()[n]
        except Exception as e:
            logger.debug(f'托盘状态计算失败: {e}')
            return '（状态读取失败）'

    def _open_settings(self):
        from core.client.settings.window import show
        show(self.app)

    def stop(self):
        """停止托盘图标"""
        if not Config.enable_tray:
            return
        try:
            from ..ui import stop_tray
            stop_tray()
            logger.info("TrayManager: 托盘图标已卸载")
        except Exception as e:
            logger.debug(f"TrayManager: 卸载托盘时发生错误: {e}")

    def _request_exit(self, icon=None, item=None):
        """托盘图标引用的退出回调"""
        logger.info("托盘退出: 用户点击退出菜单，准备清理资源并退出")
        self.app.stop()
