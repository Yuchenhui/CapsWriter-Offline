# 设置窗口正式接入 Implementation Plan

> **For agentic workers:** 逐 task 实现 —— 优先每个 task 派一个 fresh subagent (Agent 工具) 实现 + task 间 review; 或在当前会话逐 task 顺序执行。Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `core/client/settings/` 的预览原型接入客户端: 托盘只留状态 + 设置 + 退出, 设置窗口里的所有操作真正生效.

**Architecture:** 设置窗口挂在 ToastManagerThread 的 Tk 根窗口下 (新增 `call_soon` 把打开操作交给 Tk 线程). 窗口通过 `Context.do(操作名, 参数)` 调 `actions.py`; 托盘和窗口共用 actions. 本地识别在客户端记用量 `local:<模型>`.

**Tech Stack:** Python 3.13, Tkinter + PIL (已打包进 start_client.exe), pystray, 无新依赖.

## Global Constraints

- 设计: `docs/specs/2026-09-25-settings-window-design.md`; 只做 Windows.
- 不引入新第三方库, 不重新打包 exe; 部署只用 `pwsh -File deploy-local.ps1` (拷 core/*.py).
- 所有 Tk 调用只在 ToastManagerThread; 其他线程经 `ToastMessageManager().call_soon(fn)`.
- 设置窗口任何异常只记日志 / 显示在分页上, 不得影响语音输入; 键盘钩子回调里不加任何工作.
- 词库只允许写 `terms.txt` / `hot.txt` / `hot-rule.txt`, 原子写 (临时文件 + os.replace), UTF-8.
- 部署会重启 CapsWriter: 部署前告知用户约 10 秒别按右 Alt.
- git 只推 origin (Yuchenhui/CapsWriter-Offline), 提交信息末尾带 Co-Authored-By 行.
- Tk 根窗口 `tk scaling` = 2 (toast_constants.TK_SCALING_FACTOR): 设置窗口字体一律用像素 (负字号), 不受缩放影响.

---

### Task 1: 字体改像素单位 (不受 tk scaling 影响)

**Files:**
- Modify: `core/client/settings/widgets.py` (`font()`)
- Modify: `core/client/settings/pages/wordlists.py` (`MONO`)
- Modify: `core/client/settings/window.py` (`__main__` 预览: 模拟根窗口 scaling=2)

**Interfaces:**
- Produces: `widgets.font(size_pt: int, bold: bool=False) -> tuple` 返回负字号 (像素), 例: `font(10)` -> `('Microsoft YaHei UI', -13)`.

- [ ] **Step 1: 改 font() 为像素字号**

```python
def font(size: int, bold: bool = False):
    """size 按 pt 写 (设计稿习惯), 转成像素负字号: 不受根窗口 tk scaling (=2) 影响, 与预览一致"""
    px = -round(size * 96 / 72)
    return (FONT, px, 'bold') if bold else (FONT, px)
```

- [ ] **Step 2: 编辑框等宽字体同样改像素**

`pages/wordlists.py`: `MONO = ('Cascadia Mono', -13)`

- [ ] **Step 3: 预览入口模拟真实缩放**

`window.py` `__main__` 中, 建窗前: 预览用独立 `tk.Tk()` 根, 执行 `root.tk.call('tk', 'scaling', 2)` 再 `open_settings(root, ctx)` (与客户端根窗口一致), 根 `withdraw()`.

- [ ] **Step 4: 截图核对**

Run: `.venv/Scripts/python.exe -m core.client.settings.window --preview --shots <scratch>\shots`
Expected: 6 张截图文字大小与此前预览 (scaling 1.33) 一致, 无放大 1.5 倍.

- [ ] **Step 5: Commit** `fix: 设置窗口字体用像素单位, 不受 tk scaling=2 影响`

---

### Task 2: actions.py + Context 真实模式 + 词库原子写

**Files:**
- Create: `core/client/settings/actions.py`
- Modify: `core/client/settings/context.py` (`Context.__init__(base_dir, readonly=False, app=None)`, `do()` 分派到 actions)
- Modify: `core/client/settings/pages/wordlists.py` (保存时传正文而不是长度)
- Test: `tests/test_settings_actions.py`

**Interfaces:**
- Produces (全部返回 bool 成功与否, 失败记日志不抛):
  - `set_theme(key: str)`, `set_engine(key: str)`, `set_polish(pid: str)`, `set_structure(on: bool)` -> 改 `ClientConfig` 对应属性 + `user_state.save()`
  - `set_local_model(base_dir, key: str)` -> `server_launcher.switch_model(base_dir, key)`
  - `set_mic(dev_id: str)` -> `mic_select.set_default(dev_id)`; `set_gain(dev_id: str, db: float)` -> `mic_select.set_gain`
  - `calibrate(app)` -> `calibration.start(app)`
  - `save_wordlist(base_dir, fname: str, text: str)` -> 白名单 + 原子写
- `Context.do(what, *args)`: readonly 只记日志返回 True; 否则 `getattr(actions, what)` 调用, 需要 base_dir / app 的由 do 补上 (`set_local_model`, `save_wordlist` 前置 `self.base`; `calibrate` 传 `self.app`).

- [ ] **Step 1: 写失败的测试**

```python
"""设置窗口操作自检: python tests/test_settings_actions.py"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.client.settings import actions
from core.client import user_state
from config_client import ClientConfig as C

saved = []
user_state.save = lambda: saved.append(dict(engine=C.asr_engine, polish=C.polish, theme=C.capsule_theme, st=C.polish_structure))

base = Path(tempfile.mkdtemp())
assert actions.save_wordlist(base, 'terms.txt', 'GUI\nHigh Speed\n')
assert (base / 'terms.txt').read_text(encoding='utf-8') == 'GUI\nHigh Speed\n'
assert not list(base.glob('*.tmp')), '原子写不留临时文件'
assert not actions.save_wordlist(base, '..\\evil.txt', 'x') and not (base.parent / 'evil.txt').exists(), '白名单外拒绝'
assert not actions.save_wordlist(base, 'config_client.py', 'x')

assert actions.set_engine('qwen-batch') and C.asr_engine == 'qwen-batch'
assert not actions.set_engine('nope') and C.asr_engine == 'qwen-batch', '未知引擎拒绝'
assert actions.set_polish('') and C.polish == ''
assert not actions.set_polish('nope')
assert actions.set_structure(False) and C.polish_structure is False
assert actions.set_theme('frost') and C.capsule_theme == 'frost'
assert not actions.set_theme('nope')
assert len(saved) == 4, saved
print('OK')
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python.exe tests/test_settings_actions.py`
Expected: `ModuleNotFoundError: ... actions`

- [ ] **Step 3: 实现 actions.py**

```python
# coding: utf-8
"""设置操作 (托盘与设置窗口共用). 都返回 bool, 失败只记日志不抛"""
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
    return True


def set_theme(key: str) -> bool:
    return key in THEMES and _set('capsule_theme', key, '胶囊主题')


def set_engine(key: str) -> bool:
    from core.client.audio.cloud_asr import ENGINES
    return key in ENGINES and _set('asr_engine', key, '识别引擎')


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


def set_gain(dev_id: str, db: float) -> bool:
    from core.client.audio import mic_select
    return bool(mic_select.set_gain(dev_id, db))


def calibrate(app) -> bool:
    from core.client import calibration
    calibration.start(app)
    return True


def save_wordlist(base_dir, fname: str, text: str) -> bool:
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
```

- [ ] **Step 4: Context 真实模式**

`context.py`:

```python
    def __init__(self, base_dir, readonly: bool = False, app=None):
        self.base = Path(base_dir)
        self.readonly = readonly
        self.app = app

    def do(self, what: str, *args) -> bool:
        if self.readonly:
            logger.info(f'[预览, 未生效] {what} {args[:2]}')
            print(f'[预览, 未生效] {what} {args[:2]}')
            return True
        from core.client.settings import actions
        if what in ('set_local_model', 'save_wordlist'):
            args = (self.base,) + args
        elif what == 'calibrate':
            args = (self.app,)
        try:
            return bool(getattr(actions, what)(*args))
        except Exception as e:
            logger.warning(f'设置操作 {what} 失败: {e}')
            return False
```

`state()`: 非只读时只读 `ClientConfig` (删除对 user_state.json 的覆盖, 那只是预览用); 只读时保持现状.

- [ ] **Step 5: 词库页传正文**

`pages/wordlists.py` `save()` 内: `if ctx.do('save_wordlist', state['file'], body):` 失败时 `status.configure(text='保存失败，见日志', fg=pal.danger)`.

- [ ] **Step 6: 运行测试通过**

Run: `.venv/Scripts/python.exe tests/test_settings_actions.py` Expected: `OK`

- [ ] **Step 7: Commit** `feat: 设置操作 actions.py (托盘与窗口共用) + 词库原子写白名单`

---

### Task 3: Tk 线程打开窗口 + 换主题后窗口重绘

**Files:**
- Modify: `core/ui/toast_manager.py` (`call_soon`, `_process_queue` 执行队列里的函数)
- Modify: `core/client/settings/window.py` (`show(app)`, `SettingsWindow.rebuild()`)
- Modify: `core/client/settings/pages/appearance.py` (选主题后调 `ctx.on_theme_changed()`)
- Modify: `core/client/settings/context.py` (`on_theme_changed` 回调属性)

**Interfaces:**
- Consumes: Task 2 `Context(base_dir, readonly, app)`.
- Produces: `ToastMessageManager.call_soon(fn: Callable[[], None]) -> None`; `core.client.settings.window.show(app) -> None` (任意线程可调).

- [ ] **Step 1: call_soon**

`toast_manager.py` `__init__` 中加 `self._calls: Queue = Queue()`; 新方法:

```python
    def call_soon(self, fn) -> None:
        """任意线程: 让 fn 在 Tk 线程执行 (随队列轮询, ≤100ms)"""
        self._calls.put(fn)
```

`_process_queue` 的 `try` 开头加:

```python
            while not self._calls.empty():
                fn = self._calls.get_nowait()
                try:
                    fn()
                except Exception as e:
                    logger.warning(f'Tk 线程任务出错: {e}')
```

- [ ] **Step 2: show(app) 与 rebuild**

`window.py`:

```python
def show(app) -> None:
    """托盘等任意线程调用: 在 Tk 线程打开 (或提到最前) 设置窗口"""
    from core.ui.toast_manager import ToastMessageManager
    from core.client.settings.context import Context
    mgr = ToastMessageManager()
    mgr.call_soon(lambda: open_settings(mgr.root, Context(app.base_dir, app=app)))
```

`SettingsWindow` 记住 `self.master = master`, `show()` 里记 `self.current = mod`, `__init__` 里设 `ctx.on_theme_changed = self.rebuild`, 并增加:

```python
    def rebuild(self):
        """换主题后按新配色重建窗口, 停在原分页 (位置 / 大小保持)"""
        global _instance
        geo, cur = self.win.geometry(), self.current
        self.win.destroy()
        _instance = SettingsWindow(self.master, self.ctx)
        _instance.win.geometry(geo)
        _instance.show(cur)
```

- [ ] **Step 3: 外观页选主题后重绘**

`appearance.py` `pick(k)`: `if ctx.do('set_theme', k) and getattr(ctx, 'on_theme_changed', None): ctx.on_theme_changed()` (用 `win.after(50, ...)` 延后, 避免在点击回调里销毁自身).

- [ ] **Step 4: 手动验证 (预览)**

Run: 预览窗口 -> 外观页点 霜白 -> 窗口变浅色且停在外观页 (预览下 do 返回 True 也会重绘).

- [ ] **Step 5: Commit** `feat: 设置窗口在 Tk 线程打开 (call_soon), 换主题后重绘`

---

### Task 4: 托盘瘦身

**Files:**
- Modify: `core/ui/tray.py` (`_TraySystem.__init__`: 支持状态行 / 默认项 / 无重启)
- Modify: `core/client/manager/tray_manager.py` (只留状态 + 设置 + 退出; 删除 _model_items / _wordlist_items / _mic_* / _polish_* / _asr_items / _asr_label / _polish_label / _theme_items / _toggle_structure / _switch_model / _start_calibration)
- Test: `tests/test_tray_status.py`

**Interfaces:**
- Consumes: Task 3 `window.show(app)`.
- Produces: `tray_manager.status_lines() -> list[str]` (3 行: 识别 / 二次整理 / 今日花费).
- tray.py `more_options` 新格式: `(文字或 callable(item), 'status')` = 不可点状态行; `(文字, fn, 'default')` = 默认项 (左键单击图标触发); 构造参数 `restart: bool = True`.

- [ ] **Step 1: 写失败的测试**

```python
"""托盘状态行自检: python tests/test_tray_status.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config_client import ClientConfig as C
from core.client.manager import tray_manager as tm

C.asr_engine, C.polish = 'qwen-batch', ''
lines = tm.status_lines()
assert lines[0] == '识别：千问 qwen3-asr-flash（在线）', lines
assert lines[1] == '二次整理：关', lines
assert lines[2].startswith('今日花费：¥'), lines
C.asr_engine, C.polish = 'local', 'deepseek'
lines = tm.status_lines()
assert lines[0].startswith('识别：本地') and lines[1] == '二次整理：DeepSeek V4 Flash', lines
print('OK')
```

- [ ] **Step 2: 运行确认失败** Expected: `AttributeError: ... status_lines`

- [ ] **Step 3: tray_manager.py 改写** (全文替换为):

```python
# coding: utf-8
import os
import time
from . import logger
from config_client import ClientConfig as Config
from core.client import server_launcher


def status_lines() -> list:
    """托盘顶部三行状态 (每次重建菜单时计算)"""
    from core.client.audio import cloud_asr
    from core.client.settings import stats
    from core.tools import asr_usage as au, polish_usage as pu
    from core.tools.polish_providers import PROVIDERS
    import datetime as dt
    key = cloud_asr.engine_key()
    if key == 'local':
        name = dict((k, n) for k, (n, _) in server_launcher.MODELS.items()).get(server_launcher.current_model(os.getcwd()), '')
        eng = f'本地 {name}'.strip()
    else:
        eng = f'{cloud_asr.ENGINES[key][0]}（在线）'
    pol = PROVIDERS[Config.polish]['name'] if Config.polish in PROVIDERS else '关'
    today = stats.periods(au.load(), dt.date.today(), pu.load())['今日']['yuan']
    return [f'识别：{eng}', f'二次整理：{pol}', f'今日花费：{au.yuan(today)}']


class TrayManager:
    """托盘: 三行状态 + 设置 (默认项, 左键单击图标也打开) + 退出. 所有设置在设置窗口里改"""
    def __init__(self, app):
        self.app = app

    @property
    def state(self):
        return self.app.state

    def start(self):
        if not Config.enable_tray:
            return
        try:
            from ..ui import enable_min_to_tray
        except ImportError as e:
            logger.warning(f"托盘模块导入失败，跳过托盘功能: {e}")
            return
        icon_path = os.path.join(self.app.base_dir, 'assets', 'icon.ico')
        opts = [(lambda _i, n=n: self._status(n), 'status') for n in range(3)]
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
        if not Config.enable_tray:
            return
        try:
            from ..ui import stop_tray
            stop_tray()
            logger.info("TrayManager: 托盘图标已卸载")
        except Exception as e:
            logger.debug(f"TrayManager: 卸载托盘时发生错误: {e}")

    def _request_exit(self, icon=None, item=None):
        logger.info("托盘退出: 用户点击退出菜单，准备清理资源并退出")
        self.app.stop()
```

(实现前 grep 确认 `_request_exit` / `state` 之外的方法没有被其他模块引用: `grep -rn "tray_manager\.\|self.tray\." core --include=*.py`.)

- [ ] **Step 4: tray.py 支持新格式**

`enable_min_to_tray(..., more_options=None, restart=True)` 把 `restart` 传给 `_TraySystem`; `_TraySystem.__init__` 的 more_options 循环改为:

```python
            for opt in more_options:
                opt_name, opt_func = opt[0], opt[1]
                if opt_func == 'status':                                  # 本地改 2026-09-25: 不可点的状态行
                    menu_items.append(item(opt_name, lambda: None, enabled=False))
                elif len(opt) > 2 and opt[2] == 'default':                # 默认项: 左键单击托盘图标触发
                    menu_items.append(item(opt_name, lambda: opt_func(), default=True))
                elif len(opt) > 2 and opt_func == 'submenu':
                    menu_items.append(item(opt_name, _dynamic_menu(opt[2])))
                elif len(opt) > 2:
                    checked = opt[2]
                    menu_items.append(item(opt_name, opt_func, checked=lambda _item, f=checked: f()))
                else:
                    menu_items.append(item(opt_name, opt_func))
        menu_items.append(pystray.Menu.SEPARATOR) if more_options else None
        if restart:
            menu_items.append(item('重启', self.on_restart))
        menu_items.append(item('退出', self.on_exit))
```

并删除开头固定的标题项 `item(f"{self.title}", ...)` (状态行已说明程序). 注意 `lambda: opt_func()` 在循环里要绑定当前值: 写成 `lambda f=opt_func: f()` 会被 pystray 当作带参回调 —— 用工厂 `(lambda f: (lambda: f()))(opt_func)`.

- [ ] **Step 5: 测试通过** Run: `.venv/Scripts/python.exe tests/test_tray_status.py` Expected: `OK`

- [ ] **Step 6: Commit** `feat: 托盘只留状态 + 设置 + 退出, 设置为默认项`

---

### Task 5: 本地识别记用量

**Files:**
- Modify: `core/client/audio/recorder.py` (finish 分支, 取到 cloud_text 之后)
- Test: 手动 (日志 + asr_usage.json)

**Interfaces:**
- Consumes: `core.tools.asr_usage.add(model, usage, seconds)`; `server_launcher.current_model(base_dir)`.
- Produces: asr_usage.json 里 `local:<model_type>` 条目 (价格 0, stats.model_name 已支持).

- [ ] **Step 1: 实现**

在 recorder.py `cloud_text = ...` 块之后、构造最终 AudioMessage 之前:

```python
                    if not cloud_text:   # 本句由本地识别 (本地引擎 / 在线失败兜底): 记用量, 统计页显示本地句数与时长
                        try:
                            from core.client import server_launcher
                            from core.tools import asr_usage
                            asr_usage.add('local:' + server_launcher.current_model(os.getcwd()), None, self._duration)
                        except Exception as e:
                            logger.debug(f'记录本地识别用量失败: {e}')
```

(确认 recorder.py 已 `import os`; 没有则加.)

- [ ] **Step 2: 编译 + 既有自检** Run: `.venv/Scripts/python.exe -m py_compile core/client/audio/recorder.py && .venv/Scripts/python.exe tests/test_carry_punc.py` Expected: `OK`

- [ ] **Step 3: Commit** `feat: 本地识别也记用量 (local:<模型>, 费用 0)`

---

### Task 6: 部署与端到端验证

**Files:**
- Modify: `C:\Users\Marshall\Source\github\pc-tweaks\windows\capswriter\README.md`, `journal/2026-09-25.md` (pc-tweaks 仓)

- [ ] **Step 1: 全部自检**

Run (fork 目录): `tests/test_settings_actions.py`, `tests/test_tray_status.py`, `tests/test_carry_punc.py`, `tests/test_self_heal.py`, `tests/test_asr_proxy.py`, `-m core.client.settings.stats`, `-m core.tools.asr_usage`, `core/tools/polish_usage.py` —— 全部 OK.

- [ ] **Step 2: 部署** (先告知用户约 10 秒别按右 Alt) `pwsh -File deploy-local.ps1`; 日志出现 `WebSocket 建立成功` 与 `托盘图标已启用`, 无 ERROR.

- [ ] **Step 3: 验证清单 (与用户一起)**
  - 托盘右键: 三行状态正确, 只有 设置… / 退出.
  - 左键单击托盘图标 -> 设置窗口打开; 再点 -> 提到最前不新开.
  - 识别引擎页切一个引擎 -> `user_state.json` 的 asr_engine 变化, 下一句日志用新引擎.
  - 外观页换主题 -> 窗口换色, 下一句胶囊换样式.
  - 词库页改术语表保存 -> 文件内容变化, 状态"已保存".
  - 用本地引擎说一句 -> `asr_usage.json` 出现 `local:qwen_asr`.
  - 语音输入全程正常 (右 Alt / 气泡 / 上屏).

- [ ] **Step 4: 同步 pc-tweaks** README 加一段「设置窗口」(入口 / 分页 / 托盘变化), journal 记当天; commit + push.
