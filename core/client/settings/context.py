# coding: utf-8
"""设置窗口的数据与操作入口. 预览模式 (readonly) 下所有操作只打日志, 不改设置、不写文件"""
import json
import logging
from pathlib import Path

logger = logging.getLogger('client.settings')
WORDLISTS = (('terms', '术语表', 'terms.txt', '识别时提示模型 / 二次整理时对照纠正; 一行一个词, # 开头为注释'),
             ('hot', '热词', 'hot.txt', '识别结果按读音纠正成这些词; 一行一个'),
             ('rule', '替换规则', 'hot-rule.txt', '正则替换, 每行 “原文 = 替换”'))


class Context:
    def __init__(self, base_dir, readonly: bool = False, app=None):
        self.base = Path(base_dir)
        self.readonly = readonly
        self.app = app
        self.on_theme_changed = None   # 窗口换主题后重绘 (window.py 设置)
        self._preview = {}             # 预览模式: 只在内存里记下的改动 (让预览也能看到换主题效果, 不写文件)

    # ---- 读 ----
    def _json(self, name) -> dict:
        try:
            return json.loads((self.base / name).read_text(encoding='utf-8'))
        except Exception:
            return {}

    def state(self) -> dict:
        from config_client import ClientConfig as C
        s = {'asr_engine': getattr(C, 'asr_engine', 'local'), 'polish': getattr(C, 'polish', ''),
             'polish_structure': getattr(C, 'polish_structure', False), 'capsule_theme': getattr(C, 'capsule_theme', 'auto'),
             'asr_fallback': list(getattr(C, 'asr_fallback', []) or [])}
        if self.readonly:                 # 预览是独立进程, 内存里的 Config 不是客户端的, 以 user_state.json 为准
            s.update(self._json('user_state.json'))
            s.update(self._preview)
        return s

    def asr_usage(self) -> dict:
        return self._json('asr_usage.json')

    def polish_usage(self) -> dict:
        return self._json('polish_usage.json')

    def local_model(self) -> str:
        from core.client import server_launcher
        return server_launcher.current_model(self.base)

    def local_models(self) -> list:
        """[(key, 名称, 已安装)]"""
        from core.client import server_launcher
        return [(k, name, (self.base / d).is_dir()) for k, (name, d) in server_launcher.MODELS.items()]

    def read_wordlist(self, fname: str) -> str:
        assert fname in {w[2] for w in WORDLISTS}
        try:
            return (self.base / fname).read_text(encoding='utf-8')
        except FileNotFoundError:
            return ''

    # ---- 写 (预览模式只记日志) ----
    def do(self, what: str, *args) -> bool:
        if self.readonly:
            logger.info(f'[预览, 未生效] {what} {args[:2]}')
            print(f'[预览, 未生效] {what} {args[:1]}')
            if what == 'set_theme':
                self._preview['capsule_theme'] = args[0]
            return True
        from core.client.settings import actions
        fn = getattr(actions, what, None)
        if fn is None:
            logger.warning(f'未知设置操作: {what}')
            return False
        if what in ('set_local_model', 'save_wordlist'):
            args = (self.base,) + args
        elif what == 'calibrate':
            args = (self.app,)
        try:
            return bool(fn(*args))
        except Exception as e:
            logger.warning(f'设置操作 {what} 失败: {e}')
            return False
