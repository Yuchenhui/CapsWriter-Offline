# coding: utf-8
"""
原生 TSF 输出路由 (output_mode='tsf', 试验性)

整段文本经 core.tools.tsf_input 的冻结 WM_COPYDATA 协议交给前台进程内的
CapsWriter.TsfBridge.V1, 由桥窗口在 TSF 激活线程上做 ITfInsertAtSelection。

行为约定 (冻结协议的一部分):
- mode != 'tsf' 时不 import tsf_input、不调用任何 Windows API (native_enabled 先行短路);
- 失败/结果未确认不重试、不回退剪贴板或模拟按键 (协议明令禁止);
- 弹既有 hint_capsule 警告; 文本由上层照常写入历史记录, 供用户复制找回;
- 阻塞的 SendMessageTimeout (至多 1.5s) 经 to_thread 放到工作线程, 不卡事件循环;
- 本模块不向外抛异常: ctypes/传输层意外错误收口为「结果未确认」。
"""

from config_client import ClientConfig as Config
from core.tools.asyncio_to_thread import to_thread
from . import logger

# status -> 用户提示 (胶囊; 与剪贴板侧「多行文本未上屏」同一口径: 去历史记录复制)
_HINTS = {
    'no-bridge': '当前窗口没有 CapsWriter Voice 输入服务，未上屏，请到历史记录复制',
    'no-foreground': '没有前台窗口，未上屏，请到历史记录复制',
    'focus-mismatch': '目标窗口焦点已变化，未上屏，请到历史记录复制',
    'no-context': '目标窗口不接受输入，未上屏，请到历史记录复制',
    'busy': '目标窗口编辑会话忙，未上屏，请到历史记录复制',
    'internal': '原生输出失败，未上屏，请到历史记录复制',
    'invalid': '文本含原生输出不支持的内容（多行/控制字符/过长），未上屏，请到历史记录复制',
    'unknown': '原生输出结果未确认，文本已存历史记录',
    'unavailable': '此环境不支持原生输出，未上屏，请到历史记录复制',
}


def _warn(hint: str) -> None:
    """弹既有胶囊警告; 界面缺席只记日志"""
    try:
        from core.client.ui.recording_toast import hint_capsule
        hint_capsule(hint, ok=False)
    except Exception as e:
        logger.debug(f'原生输出提示失败: {e}')


def native_enabled() -> bool:
    """是否启用原生 TSF 输出。只读配置枚举, 不触碰任何 Windows API。"""
    return getattr(Config, 'output_mode', 'clipboard') == 'tsf'


async def output_native(text: str) -> bool:
    """原生模式输出一次。返回 True=已插入; False=失败或结果未确认 (调用方不得回退)。

    关闭状态立即返回, 不加载 tsf_input / Windows API。本函数不抛异常:
    ctypes/API 层的意外错误按「结果未确认」收口, 让上层照常走 set_output_text/历史。"""
    if not native_enabled():
        logger.debug('原生输出未启用, 跳过')
        return False
    try:
        from core.tools import tsf_input
        status, detail = await to_thread(tsf_input.send_text, text)
    except Exception as e:
        # 窄边界: Win32/ctypes/传输层的意外异常按「结果未确认」处理, 不重试不回退,
        # 也不让异常逃出去跳过上层的历史记录
        logger.warning(f'原生 TSF 输出异常, 结果未确认: {e!r}')
        _warn(_HINTS['unknown'])
        return False
    if status == tsf_input.ST_INSERTED:
        logger.info(f'原生 TSF 输出成功 ({len(text)} 字)')
        return True
    logger.warning(f'原生 TSF 输出未上屏: status={status}, detail={detail!r}')
    _warn(_HINTS.get(status, '原生输出未上屏，请到历史记录复制'))
    return False
