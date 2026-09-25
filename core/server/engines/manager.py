import time
from .factory import EngineFactory
from .base import BaseAlignEngine
from . import logger

class ManagedAlignerProxy(BaseAlignEngine):
    """
    对齐引擎托管代理
    
    实现“懒加载”与“闲置卸载”逻辑。
    由于 TaskHandler 是单线程同步的，此处无需线程锁。
    """

    def __init__(self, timeout_sec=600):
        self.engine = None
        self.timeout = timeout_sec
        self.last_active = time.time()
        self.is_processing = False

    def align(self, audio, text, **kwargs):
        # 1. 懒加载
        if self.engine is None:
            logger.info("🚩 [AlignerProxy] 检测到文件任务需求，正在即时加载对齐引擎...")
            self.engine = EngineFactory.create_align_engine()
        
        # 2. 标记运行并执行
        self.is_processing = True
        try:
            res = self.engine.align(audio, text, **kwargs)
            self.last_active = time.time()
            return res
        finally:
            self.is_processing = False

    def check_idle(self):
        """ 闲置检查：由外部循环在空闲时调用 """
        if self.timeout <= 0 or self.is_processing or self.engine is None:
            return

        idle_time = time.time() - self.last_active
        if idle_time > self.timeout:
            logger.info(f"🚩 [AlignerProxy] 对齐引擎已闲置 {idle_time:.0f}s，正在自动卸载以释放显存...")
            self.engine.cleanup()
            self.engine = None

    def cleanup(self):
        if self.engine:
            self.engine.cleanup()
            self.engine = None


class ManagedASRProxy:
    """
    本地识别引擎托管代理 (本地改 2026-09-25)

    用在线识别时本地模型只是兜底, 却常驻约 2.4GB 显存. 最近一句用的是在线结果 (mark(online=True)) 且本地模型闲置
    超过 timeout 秒 -> 卸载; 之后任何一句需要本地识别 (在线失败兜底 / 切回本地), 访问引擎属性时自动重新加载 (~5s).
    最近一句是本地识别时永不卸载, 本地用户不受影响.
    实测 (同进程): 加载后 2450MB -> 卸载后 71MB -> 重新加载 5.2s -> 识别正常 -> 再卸载 72MB.
    TaskHandler 单线程调用, 无需加锁.
    """

    def __init__(self, factory, timeout_sec: float):
        self._factory, self.timeout = factory, timeout_sec
        self.engine = factory()
        self.last_active = time.time()
        self.online = False

    def __getattr__(self, name):          # 只在代理自身没有该属性时调用: 转发给引擎, 必要时先加载
        if name.startswith('_') or name in ('engine', 'timeout', 'last_active', 'online'):
            raise AttributeError(name)
        if self.engine is None:
            t = time.time()
            logger.info('本地识别模型已卸载, 本句需要本地识别, 重新加载...')
            self.engine = self._factory()
            logger.info(f'本地识别模型重新加载完成, 耗时 {time.time() - t:.1f}s')
        self.last_active = time.time()
        return getattr(self.engine, name)

    def mark(self, online: bool) -> None:
        self.online = online
        self.last_active = time.time()

    def check_idle(self) -> None:
        """由 TaskHandler 空闲循环调用"""
        if self.engine is None or not self.online or self.timeout <= 0:
            return
        idle = time.time() - self.last_active
        if idle > self.timeout:
            logger.info(f'正在用在线识别, 本地识别模型已闲置 {idle:.0f}s, 卸载以释放显存')
            self.engine.cleanup()
            self.engine = None

    def cleanup(self):
        if self.engine is not None:
            self.engine.cleanup()
            self.engine = None
