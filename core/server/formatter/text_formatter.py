# coding: utf-8
"""
文本格式化处理器

负责对识别出的原始文本进行后期处理，如标点补全、ITN转换等。
"""

import re

from core.tools.chinese_itn import chinese_to_num
from core.tools.format_tools import adjust_space
from config_server import ServerConfig as Config
from . import logger

# 本地改: IP 地址单独处理. ITN 认不出 "幺七二点幺六点一百点幺零三" 这种逐位与"一百"混说的写法
_CN = '[零幺一二两三四五六七八九十百]+'
_IP = re.compile(rf'{_CN}(?:点{_CN}){{3}}')


def _ip(m: re.Match) -> str:
    parts = [chinese_to_num(p) for p in m.group().split('点')]
    if all(p.isdigit() and int(p) <= 255 for p in parts):
        return '.'.join(parts)
    return m.group()


# 本地改: "千问/千万三点八" 是 Qwen 型号 (热词规则在客户端再转), ITN 会先把它变成 10003000.8, 这段原样跳过
_QWEN = re.compile(r'(?<![一二两三四五六七八九十百千万亿零0-9])千\s*[问万文闻分温]\s*[一二三四五六七八九](?:\s*点\s*[零一二三四五六七八九])?')


def _itn(text: str) -> str:
    out, i = [], 0
    for m in _QWEN.finditer(text):
        out += [chinese_to_num(_IP.sub(_ip, text[i:m.start()])), m.group()]
        i = m.end()
    out.append(chinese_to_num(_IP.sub(_ip, text[i:])))
    return ''.join(out)


class TextFormatter:
    """
    文本格式化处理器
    
    整合多种文本处理工具，提供统一的格式化接口。
    """
    def __init__(self, punc_model=None):
        """
        初始化格式化器
        
        Args:
            punc_model: 标点预测模型实例（可选）
        """
        self.punc_model = punc_model

    def format(self, text: str) -> str:
        """
        对输入文本应用一组格式化规则

        流程：
        1. 自动补全标点符号 (punc_model.punctuate)
        2. 处理 ITN (中文数字转阿拉伯数字)
        3. 调整中英文/数字间的空格 (adjust_space)
        
        Args:
            text: 原始待处理文本

        Returns:
            处理完成的格式化文本
        """
        if not text:
            return ""

        # 1. 增加标点
        if self.punc_model:
            try:
                # 调用标准化 PuncEngine 接口
                text = self.punc_model.punctuate(text)
            except Exception as e:
                logger.warning(f"标点补全失败: {e}")

        # 2. 中文数字转阿拉伯数字
        if Config.format_num:
            try:
                text = _itn(text)
            except Exception as e:
                logger.warning(f"ITN 转换失败: {e}")
        
        # 3. 调整中英文空格（ITN 之后，中英边界清晰，一次调整到位）
        if Config.format_spell:
            text = adjust_space(text)

        return text
