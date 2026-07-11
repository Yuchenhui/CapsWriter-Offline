# coding: utf-8
"""ToastWindowRecording「转写中」状态单元测试(需要桌面环境,本机可跑)"""
import time
import tkinter as tk

import pytest

from core.ui.toast_recording import ToastWindowRecording, _PROC_LABEL


def _pump(root, seconds: float) -> None:
    """驱动 Tk 事件循环指定时长,让 after 回调有机会执行"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        root.update()
        time.sleep(0.02)


@pytest.fixture(scope='module')
def tk_root():
    # 模块级共享一个 Tk 根：Windows 上快速反复创建/销毁 Tk() 偶发
    # "Can't find a usable init.tcl"，各测试只建/销自己的 Toplevel
    root = tk.Tk()
    root.withdraw()
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass


def test_starts_in_listening_mode(tk_root):
    w = ToastWindowRecording(tk_root, '正在聆听')
    assert w._applied_mode == 'listening'
    w.window.destroy()


def test_update_text_switches_to_processing(tk_root):
    w = ToastWindowRecording(tk_root, '正在聆听')
    w.update_text(_PROC_LABEL)
    _pump(tk_root, 0.3)             # 让 _tick 应用切换
    assert w._applied_mode == 'processing'
    assert w._text == _PROC_LABEL
    assert w.window.winfo_exists()  # 切换后窗口仍然存活
    w.window.destroy()


def test_any_update_text_switches_to_processing(tk_root):
    """recording 窗口的唯一更新语义是状态切换:任意文本都触发,与文案解耦"""
    w = ToastWindowRecording(tk_root, '正在聆听')
    w.update_text('随便什么文本')
    _pump(tk_root, 0.2)
    assert w._applied_mode == 'processing'
    assert w._text == _PROC_LABEL    # 展示文案由窗口自持,不受传入内容影响
    w.window.destroy()


def test_processing_times_out_and_self_destroys(tk_root):
    w = ToastWindowRecording(tk_root, '正在聆听')
    w._proc_timeout_ms = 120        # 注入极短超时,避免真等 15 秒
    w.update_text(_PROC_LABEL)
    _pump(tk_root, 0.8)
    assert not w.window.winfo_exists()


def test_timeout_invokes_stop_callback(tk_root):
    """超时自毁前必须回调 stop_callback,让持有者回收注册状态(避免 stale _active)"""
    called = []
    w = ToastWindowRecording(tk_root, '正在聆听',
                             stop_callback=lambda: called.append(True))
    w._proc_timeout_ms = 120
    w.update_text(_PROC_LABEL)
    _pump(tk_root, 0.8)
    assert called == [True]
    assert not w.window.winfo_exists()


def test_normal_close_does_not_fire_timeout_callback(tk_root):
    """正常关闭后,已排定的超时 after 不得再触发 stop_callback(否则会误清新胶囊的注册)"""
    called = []
    w = ToastWindowRecording(tk_root, '正在聆听',
                             stop_callback=lambda: called.append(True))
    w._proc_timeout_ms = 200
    w.update_text(_PROC_LABEL)
    _pump(tk_root, 0.1)              # 让切换与 after 排定生效
    w.window.destroy()               # 正常关闭(模拟 close_toast)
    _pump(tk_root, 0.5)              # 越过超时点
    assert called == []


def test_manager_close_before_window_creation_leaves_no_orphan():
    """close_toast 赶在 Tk 线程建窗之前到达时,消息应被取消,不产生孤儿胶囊"""
    from core.ui.toast import ToastMessageManager, ToastMessage
    mgr = ToastMessageManager()
    msg = ToastMessage(text='正在聆听', streaming=True, window_type='recording')
    msg_id = mgr.add_message(msg)
    mgr.close_toast(msg_id)          # 立即关闭,大概率早于 100ms 队列轮询建窗
    time.sleep(1.0)                  # 给足轮询周期
    assert all(getattr(w, '_msg_id', None) != msg_id for w in mgr.active_windows)
