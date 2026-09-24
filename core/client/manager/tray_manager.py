# coding: utf-8
import os
from . import logger
import os, sys, subprocess, time
from config_client import ClientConfig as Config
from core.client import server_launcher, user_state


class TrayManager:
    """
    托盘管理器：负责系统托盘图标的初始化、菜单构建及回调处理。
    """
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

        # 获取图标路径
        icon_path = os.path.join(self.app.base_dir, 'assets', 'icon.ico')
        
        # 启用托盘
        enable_min_to_tray(
            'CapsWriter Client',
            icon_path,
            exit_callback=self.app.stop,
            more_options=[
                # 本地改 (2026-09-23 精简): 去掉 日记 (录音已关) / 上下文 (由 terms.txt 取代) / 清除记忆 (LLM 已关) /
                # 重开音频 (流失效自动重开 + 麦克风菜单 + 重启 已覆盖); 模型、词库改二级菜单
                ('复制结果', self._copy_last_result),
                ('剪贴板 → 术语表', self._add_term_from_clipboard),
                ('二次整理', 'submenu', self._polish_items),
                ('麦克风', 'submenu', self._mic_items),
                ('麦克风校准…', self._start_calibration),
                ('胶囊主题', 'submenu', self._theme_items),
                ('模型', 'submenu', self._model_items),
                ('词库', 'submenu', self._wordlist_items),
            ]
        )
        logger.info("托盘图标已启用")

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

    def _model_items(self):
        """托盘「模型」子菜单"""
        return [self._model_item(mt, name) for mt, (name, _) in server_launcher.MODELS.items()]

    def _model_item(self, model_type, name):
        """托盘单选项. 回调必须零参数闭包: pystray 按参数个数决定是否传 (icon, item), 带默认参数的 lambda 会被覆盖"""
        def action():
            self._switch_model(model_type)
        def checked():
            return server_launcher.current_model(self.app.base_dir) == model_type
        return (name, action, checked)

    # 托盘「词库」子菜单: 点了用系统默认编辑器打开, 保存即生效 (不用重启)
    _WORDLISTS = (('术语表 (terms.txt)', 'terms.txt'),        # 识别前作为上下文提示模型
                  ('热词 (hot.txt)', 'hot.txt'),              # 识别后按读音相似度纠正
                  ('替换规则 (hot-rule.txt)', 'hot-rule.txt'))   # 识别后按正则精确替换

    def _wordlist_items(self):
        return [(label, self._open_file(fname), lambda: None) for label, fname in self._WORDLISTS]

    def _open_file(self, fname):
        def action():
            os.startfile(os.path.join(self.app.base_dir, fname))
        return action

    def _mic_items(self):
        """托盘「麦克风」子菜单: 可用录音设备, 打勾 = 当前 Windows 默认 (CapsWriter 跟随它)"""
        from core.client.audio import mic_select
        from core.client.audio.default_device_watch import default_capture_id
        devs = mic_select.list_capture()   # 先调它: 内部会在托盘线程里 CoInitialize
        cur = default_capture_id()
        if not devs:
            return [('（没有可用的录音设备）', lambda: None, lambda: False)]
        return [(name + ('  [已静音]' if muted else ''), 'submenu', self._mic_device_items(dev_id, name),
                 self._mic_checked(dev_id, cur))
                for dev_id, name, muted, _gain in devs]

    def _mic_device_items(self, dev_id, name):
        """某个麦克风的子菜单: 设为当前 + 增益档位 (Windows 按设备保存增益, 各设备互不影响)"""
        def items():
            from core.client.audio import mic_select
            out = [('使用这个麦克风', self._mic_action(dev_id, name), lambda: None)]
            gi = mic_select.gain_info(dev_id)
            if gi and gi[0] is not None:
                cur_db, mn, mx, inc = gi
                out.append(None)
                out.append((f'当前增益 {cur_db:+g} dB', lambda: None, lambda: None))
                for db in mic_select.gain_steps(mn, mx, inc):
                    out.append((f'增益 {db:+g} dB', self._gain_action(dev_id, name, db), self._gain_checked(cur_db, db, inc)))
            return out
        return items

    @staticmethod
    def _gain_checked(cur_db, db, inc):
        def checked():
            return abs(cur_db - db) < max(inc, 0.5) / 2 + 1e-6
        return checked

    @staticmethod
    def _gain_action(dev_id, name, db):
        def action():
            from core.client.audio import mic_select
            if mic_select.set_gain(dev_id, db):
                logger.info(f'托盘设置麦克风增益: {name} -> {db:+g} dB')
        return action

    @staticmethod
    def _mic_checked(dev_id, cur):
        def checked():
            return dev_id == cur
        return checked

    @staticmethod
    def _mic_action(dev_id, name):
        """零参数闭包 (pystray 按参数个数决定传不传 icon/item)"""
        def action():
            from core.client.audio import mic_select
            if mic_select.set_default(dev_id):
                logger.info(f'托盘选择麦克风: {name} (已设为 Windows 默认录音设备, 2 秒内生效)')
        return action

    def _switch_model(self, model_type):
        """托盘切换识别模型: 改 config_server.py 并重启服务端, 约 5-15 秒后生效"""
        server_launcher.switch_model(self.app.base_dir, model_type)

    def _polish_items(self):
        """托盘「二次整理」子菜单: 关 / 各服务商, 单选, 下一句起生效, 存 user_state.json"""
        from core.tools.polish_providers import PROVIDERS
        return [('关', self._polish_action(''), lambda: not Config.polish)] + [
            (p['name'], self._polish_action(pid), self._polish_checked(pid)) for pid, p in PROVIDERS.items()] + [
            None, ('结构化整理（编号 / 换行）', self._toggle_structure, lambda: Config.polish_structure)]

    @staticmethod
    def _theme_items():
        """托盘「胶囊主题」: 跟随系统 / 浅色 / 深色, 单选, 下一句起生效, 存 user_state.json"""
        def pick(v):
            def action():
                Config.capsule_theme = v
                user_state.save()
                logger.info(f'胶囊主题: {v}')
            return action
        items = [(label, pick(v), lambda v=v: Config.capsule_theme == v)
                 for label, v in (('跟随系统', 'auto'), ('浅色', 'light'), ('深色', 'dark'))]
        from core.ui.capsule_themes import THEMES   # 设计包主题 (带完成动画)
        return items + [None] + [(cls.name, pick(k), lambda k=k: Config.capsule_theme == k) for k, cls in THEMES.items()]

    @staticmethod
    def _toggle_structure():
        Config.polish_structure = not Config.polish_structure
        user_state.save()
        logger.info(f"结构化整理: {'开' if Config.polish_structure else '关'}")

    @staticmethod
    def _polish_checked(pid):
        def checked():
            return Config.polish == pid
        return checked

    @staticmethod
    def _polish_action(pid):
        def action():
            Config.polish = pid
            user_state.save()
            logger.info(f"二次整理: {pid or '关'}")
        return action

    def _copy_last_result(self):
        """复制最后一次识别结果到剪贴板回调"""
        text = self.state.last_output_text
        if text:
            from ..llm.llm_clipboard import copy_to_clipboard
            copy_to_clipboard(text)

    def _start_calibration(self):
        """读几句校准文本, 自动设当前麦克风的增益 (core/client/calibration.py)"""
        from core.client import calibration
        calibration.start(self.app)

    def _add_term_from_clipboard(self):
        """把剪贴板里的词加进 terms.txt (识别时作上下文 + 二次整理词表, 保存即生效). 用法: 选中正确的词 Ctrl+C, 点这里"""
        from ..ui import toast
        word = add_term(os.path.join(self.app.base_dir, 'terms.txt'), _read_clipboard())
        if word is None:
            toast('剪贴板里要是一个词 (一行, 40 字以内): 先选中正确的写法按 Ctrl+C', duration=3500)
        elif word.startswith('已有:'):
            toast(f'术语表里{word}', duration=2500, bg='#075077')
        elif word.startswith('失败:'):
            toast(f'写术语表{word}', duration=3500)
        else:
            logger.info(f'术语表新增: {word}')
            toast(f'已加入术语表: {word}', duration=2500, bg='#1B7F3B')

    def _request_exit(self, icon=None, item=None):
        """托盘图标引用的退出回调"""
        logger.info("托盘退出: 用户点击退出菜单，准备清理资源并退出")
        self.app.stop()


def _read_clipboard() -> str:
    try:
        import pyclip
        return pyclip.paste().decode('utf-8', errors='replace')
    except Exception as e:
        logger.warning(f'读剪贴板失败: {e}')
        return ''


def add_term(path: str, raw: str):
    """把一个词追加到术语表. 返回: 词 = 已加; '已有: 词'; '失败: 原因'; None = 不像一个词 (空 / 多行 / 超 40 字)"""
    word = (raw or '').strip()
    if not word or '\n' in word or '\r' in word or len(word) > 40:
        return None
    try:
        lines = open(path, encoding='utf-8').read().splitlines() if os.path.exists(path) else []
        if word in (l.strip() for l in lines):
            return f'已有: {word}'
        with open(path, 'a', encoding='utf-8') as f:
            f.write(('\n' if lines and not open(path, encoding='utf-8').read().endswith('\n') else '') + word + '\n')
    except OSError as e:
        return f'失败: {e}'
    return word


if __name__ == '__main__':   # 自检: 临时文件上跑 add_term
    import tempfile
    d = tempfile.mkdtemp(); f = os.path.join(d, 'terms.txt')
    open(f, 'w', encoding='utf-8').write('WSL\nDebian')          # 末尾无换行
    assert add_term(f, '  VoiceInk ') == 'VoiceInk'
    assert add_term(f, 'VoiceInk') == '已有: VoiceInk'
    assert add_term(f, 'a\nb') is None and add_term(f, '') is None and add_term(f, 'x' * 41) is None
    assert open(f, encoding='utf-8').read() == 'WSL\nDebian\nVoiceInk\n', repr(open(f, encoding='utf-8').read())
    print('add_term selftest ok')
