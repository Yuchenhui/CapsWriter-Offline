# coding: utf-8
import asyncio
from . import logger
from ..ui import TipsDisplay
from config_client import ClientConfig as Config, __version__


def _prewarm_ui() -> None:
    """启动 Tk 界面线程并等它就绪, 预加载录音胶囊用到的模块 (PIL 等)"""
    import time
    t0 = time.perf_counter()
    try:
        from core.ui.toast_manager import ToastMessageManager
        m = ToastMessageManager()
        for _ in range(100):             # 最多等 3 秒
            if m.is_running:
                break
            time.sleep(0.03)
        import core.ui.toast_recording, core.ui.layered_renderer, core.ui.recording_level   # noqa: F401 胶囊首次创建要用
        from core.client.ui import recording_toast                                           # noqa: F401
        logger.info(f'界面预热完成 {(time.perf_counter() - t0) * 1000:.0f}ms (在装键盘钩子之前)')
    except Exception as e:
        logger.warning(f'界面预热失败 (不影响使用, 但第一次按键可能卡住钩子): {e}')


class MicRunner:
    """
    麦克风模式运行器：负责麦克风模式下的资源初始化、识别处理器循环及生命周期监控。
    """
    def __init__(self, app):
        self.app = app
        self.processor = None

    @property
    def state(self):
        return self.app.state

    @property
    def ws_manager(self):
        return self.app.ws

    @property
    def tray_manager(self):
        return self.app.tray

    def start_resources(self):
        """初始化麦克风模式特有资源 (音频硬件、快捷键、UI 托盘)"""
        # 0. 恢复托盘里改过的开关 (user_state.json), 再托管服务端 (隐藏子进程, 客户端即整个程序)
        from core.client import user_state
        user_state.load()
        from core.client import server_launcher
        server_launcher.start(self.app.base_dir)

        # 1. 托盘
        self.tray_manager.start()

        # 2. UI 提示
        TipsDisplay.show_mic_tips()

        # 2.5 本地改 (2026-09-24 卡键): 界面预热必须在装键盘钩子之前.
        # 实测启动后第一次按键要在钩子里初始化 Tk 界面线程, 公司机器上耗时 470-506ms, 占着 GIL 让钩子等,
        # 右 Alt 的按下漏进系统. 提前做掉, 第一次按键就没有重活了.
        _prewarm_ui()

        # 3. 开启运行组件 (音频流、快捷键监听)
        self.app.stream.start()
        self.app.shortcut.start()
        if getattr(Config, 'follow_default_mic', True):
            from core.client.audio import default_device_watch
            default_device_watch.start(self.app)   # 系统里换了默认麦克风就自动跟过去
        from core.client.audio import idle_release
        idle_release.schedule(self.app)   # 启动后一直没人说话也按时释放麦克风
        
        # 4. 开启 UDP 控制 (如果启用)
        if Config.udp_control:
            self.app.udp.start()

        # 5. 开启后台服务 (热词、LLM)
        self.app.hotword.start()
        self.app.llm.start()

    async def run(self):
        """麦克风模式主入口"""
        
        logger.info("=" * 50)
        logger.info(f"CapsWriter Offline Client {__version__} (麦克风模式)")
        logger.info(f"日志级别: {Config.log_level}")
        
        # 1. 资源启动
        self.start_resources()
        
        # 2. 启动核心处理器 (内部处理连接与循环)
        
        from ..output import ResultProcessor
        self.processor = ResultProcessor(self.app)
        await self.processor.start()
            

