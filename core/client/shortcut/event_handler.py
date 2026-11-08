# coding: utf-8
"""
事件处理器

处理键盘和鼠标事件的逻辑
"""

import time
from . import logger



class ShortcutEventHandler:
    """
    快捷键事件处理器

    处理按键按下和释放的逻辑，包括录音启动、取消、完成等
    """

    def __init__(self, tasks, pool, emulator):
        """
        初始化事件处理器

        Args:
            tasks: 快捷键任务字典
            pool: 线程池
            emulator: 快捷键模拟器
        """
        self.tasks = tasks
        self.pool = pool
        self.emulator = emulator

    def handle_keydown(self, key_name, task) -> None:
        """处理按键按下事件"""
        # 长按模式
        if task.shortcut.hold_mode:
            task.last_down = time.monotonic()
            if task.capped:   # 到时长上限后, 松开前的按下 (含自动重复) 都不开录
                return
            if not task.is_recording:
                task.launch()
            return

        # 单击模式
        if task.released:
            from threading import Event
            task.pressed = True
            task.released = False
            task.event = Event()  # 创建新事件对象
            self.pool.submit(self._count_down, task)
            self.pool.submit(self._manage_task, task)

    def handle_keyup(self, key_name, task) -> None:
        """处理按键释放事件"""
        # 单击模式
        if not task.shortcut.hold_mode:
            if task.pressed:
                task.pressed = False
                task.released = True
                task.event.set()
            return

        # 长按模式
        task.capped = False
        lost_up, task.lost_up = task.lost_up, False
        if not task.is_recording:
            return

        duration = time.time() - task.recording_start_time
        logger.debug(f"[{key_name}] 松开，持续时间: {duration:.2f}s")

        if duration < task.threshold:
            self._handle_short_press(key_name, task, emulate=not lost_up)
        else:
            task.finish()

    def _handle_short_press(self, key_name: str, task, emulate: bool = True) -> None:
        """处理短按情况.

        本地改 2026-11-08: 不再补发按键. 原来短按取消后 emulate 补发一次按下+松开
        (本意是不吞用户按键), 但补发的键又被自家钩子捕成第二次按下 -> 录音再次触发,
        表现为「没说话就松开, 卡住一会儿, 必须再敲一下才继续」(日志实例:
        19:28:18 短按 0.14s -> 取消 -> 补发 -> 0.36s 后又开录 17s).
        RAlt 的默认行为 (切窗口菜单) 在按住说话场景没有用途, 吞掉即消化.

        emulate 参数保留 (自动重复中断场景由调用方显式传 False), 但已不驱动补发.
        """
        cancel_start = time.perf_counter()
        task.cancel()
        cancel_time = (time.perf_counter() - cancel_start) * 1000
        logger.debug(f"[{key_name}] task.cancel() 耗时: {cancel_time:.2f}ms")
        logger.debug(f"[{key_name}] 短按取消, 不补发按键 (本地改 2026-11-08: 补发会被钩子捕成二次触发)")

    def _count_down(self, task) -> None:
        """倒计时（单击模式）"""
        time.sleep(task.threshold)
        task.event.set()

    def _manage_task(self, task) -> None:
        """管理录音任务（单击模式）"""
        was_recording = task.is_recording

        if not was_recording:
            task.launch()

        if task.event.wait(timeout=task.threshold * 0.8):
            if task.is_recording and was_recording:
                task.finish()
        else:
            if not was_recording:
                task.cancel()
