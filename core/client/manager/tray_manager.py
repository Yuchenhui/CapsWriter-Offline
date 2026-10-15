# coding: utf-8
"""
托盘 (本地改 2026-09-25): 三行状态 + 「复制最新一句」+「设置…」+「退出」.
所有设置都在设置窗口 (core/client/settings) 里改; 原来的各设置子菜单已移除.

本地改 2026-10-15: 图标左键快捷操作 —— 单击复制最新一句历史 (toast 提示), 双击打开设置窗口.
pystray 对单击/双击都只触发默认菜单项, 这里按 GetDoubleClickTime 自行判别.
"""
import datetime as dt
import os
import threading

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
        self._click_lock = threading.Lock()
        self._click_timer = None       # 单击延时计时器; 双击到达时取消

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
        opts.append(('左键单击图标＝复制最新一句，双击＝设置', 'status'))
        opts.append(('复制最新一句', self._on_icon_click, 'default'))
        opts.append(('设置…', self._open_settings))
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

    @staticmethod
    def _double_click_time() -> float:
        """系统双击间隔 (秒) + 余量; 读不到就用 0.55"""
        try:
            import ctypes
            return ctypes.windll.user32.GetDoubleClickTime() / 1000 + 0.05
        except Exception:
            return 0.55

    def _on_icon_click(self):
        """托盘图标左键 (pystray 单击/双击都走到这里): 单击延时复制最新一句, 双击取消复制改开设置"""
        with self._click_lock:
            t = self._click_timer
            if t is not None and t.is_alive():
                t.cancel()
                self._click_timer = None
                self._open_settings()           # 双击
                return
            self._click_timer = threading.Timer(self._double_click_time(), self._copy_latest)
            self._click_timer.daemon = True
            self._click_timer.start()

    def _copy_latest(self):
        """复制 history.jsonl 里最新一句并弹胶囊提示; 失败只记日志"""
        try:
            from core.client import history
            from core.client.clipboard import copy_to_clipboard
            from core.client.ui.recording_toast import hint_capsule
            entries = history.read_entries(self.app.base_dir, 1)
            if not entries:
                hint_capsule('还没有历史记录', ok=False)
                return
            text = entries[0]['text']
            copy_to_clipboard(text)
            preview = text if len(text) <= 14 else text[:14] + '…'
            hint_capsule(f'已复制：{preview}')
            logger.info(f'托盘复制最新一句 ({len(text)} 字)')
        except Exception as e:
            logger.warning(f'托盘复制最新一句失败: {e}')

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
