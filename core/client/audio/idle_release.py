"""
麦克风闲置释放: 最后一次录音结束 N 秒后关掉音频流 (麦克风占用指示灭、不挡自动睡眠), 下次按键再打开.

代价: 冷启动打开约 0.5s (本机无线麦实测 476ms; 刚用过时 ~50ms), 这期间没有音频. 胶囊在拿不到电平时显示
"预热"暗点 (toast_recording), 看到声波跳了再开口就不会吞字. mic_idle_release_sec = 0 关闭本功能 (麦克风常开).
"""
import logging
import threading

from config_client import ClientConfig as Config

logger = logging.getLogger('client.' + __name__.rsplit('.', 1)[-1])   # 挂到 client 下才会写进 client_latest.log

_timer = None
_lock = threading.Lock()


def _seconds() -> float:
    return float(getattr(Config, 'mic_idle_release_sec', 0) or 0)


def wake(app) -> None:
    """按下录音键: 取消释放计时; 流已关则在后台线程重新打开 (start() 要几百 ms, 不能堵键盘钩子)."""
    global _timer
    if _seconds() <= 0:
        if app.stream.is_stale(1.0):
            check_stale(app)
        return
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
    if not app.stream._running:
        logger.info('麦克风闲置已释放, 重新打开')
        threading.Thread(target=app.stream.start, daemon=True, name='mic-wake').start()
    elif app.stream.is_stale(1.0):
        check_stale(app)


def check_stale(app) -> None:
    """本地改 F5: 流标着在运行却超过 1s 没回调 (锁屏/睡眠/驱动重置后) -> 后台重开. 按下录音键时调用"""
    logger.warning('音频流在运行但已无数据回调 (锁屏/睡眠后失效?), 后台重开')
    threading.Thread(target=app.stream.reopen, daemon=True, name='mic-reopen').start()


def schedule(app) -> None:
    """录音结束: N 秒后若仍未在录音, 关掉音频流."""
    global _timer
    sec = _seconds()
    if sec <= 0:
        return

    def release():
        global _timer
        with _lock:
            _timer = None
        if _seconds() <= 0 or app.state.recording or not app.stream._running:   # 计时期间被设置页关掉了
            return
        logger.info(f'麦克风闲置 {sec:.0f}s, 释放音频流')
        try:
            app.stream.stop()
        except Exception as e:
            logger.warning(f'释放音频流失败: {e}')

    with _lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(sec, release)
        _timer.daemon = True
        _timer.start()
