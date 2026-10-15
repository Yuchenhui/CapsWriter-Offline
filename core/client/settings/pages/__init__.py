# coding: utf-8
"""设置窗口分页 (顺序即导航顺序): 每个模块提供 TITLE 与 build(parent, pal, ctx) -> Frame"""
from . import history_page, stats_page, appearance, wordlists, engine, mic, system, about

PAGES = (history_page, stats_page, appearance, wordlists, engine, mic, system, about)
