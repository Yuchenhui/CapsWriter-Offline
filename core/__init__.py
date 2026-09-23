import os as _os
_os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')   # 本地改: numpy/OpenBLAS 按 24 核预分配 ~750MB 提交内存/进程; 本项目只用它算梅尔滤波, 单线程足够
# coding: utf-8

from .logger import get_logger, setup_logger

import colorama
colorama.init()