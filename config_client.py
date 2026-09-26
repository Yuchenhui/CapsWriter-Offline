import os
from collections.abc import Iterable
from pathlib import Path

# 版本信息
__version__ = '2.6'

# 项目根目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# 客户端配置
class ClientConfig:
    addr = '127.0.0.1'          # Server 地址
    port = '6016'               # Server 端口

    # 快捷键配置列表
    shortcuts = [
        {
            'key': 'alt_gr',        # 右 Alt: 按住说话, 松开上屏 (不再用 CapsLock)
            'type': 'keyboard',     # 是键盘快捷键
            'suppress': True,       # 阻塞按键（短按会补发）; 也让 WeType/AHK 收不到长按
            'hold_mode': True,      # 长按模式
            'enabled': True         # 启用此快捷键
        },
        {
            'key': 'x2',
            'type': 'mouse',
            'suppress': True,
            'hold_mode': True,
            'enabled': True
        },
    ]

    threshold    = 0.3          # 快捷键触发阈值（秒）
    silence_rms_gate = 0.0056   # 整句平均音量低于此值 (-45 dBFS) 视为没说话, 不送识别; 0 = 关.
                                # 依据 2026-09-23 实测: 正常说话平均 -20 ~ -38.6 dBFS, 没说话误识别 -53.5 / -50.1 dBFS
    silence_gate_hold = 10      # 门限开启时, 前 N 秒音频留在客户端等松开再判断 (更长的录音照常边录边发, 不做门限)

    paste        = True         # 走剪贴板+Ctrl-V: 模拟逐字键入会经过 WeType IME, 在 VS Code 终端里重复
    restore_clip = False        # 模拟粘贴后是否恢复剪贴板. 本地改 (2026-09-23): 关 —— 识别结果一直留在剪贴板 (并进 Win+V 历史),
                                # 光标不在输入框导致没粘上时换个地方 Ctrl+V 即可, 不丢字
    paste_apps   = ['WeiXin.exe', 'Telegram.exe']  # 匹配时强制粘贴

    enter_apps   = [('happ.exe', 0.5), ('hexin.exe', 0.5)]  # (应用名, 延迟秒数) 输出完成后自动回车，如同花顺，输入股票名后，需要回车才能切换

    save_audio = False          # 是否保存录音文件 (关: 每句一个 WAV, 越积越多)
    audio_name_len = 20         # 将录音识别结果的前多少个字存储到录音文件名中，建议不要超过200

    # 录音胶囊 (移植自 rockbenben/CapsWriter-Offline 09eb846/d5380b6/ca8b1ca)
    show_recording_toast = True  # 录音时屏幕底部显示深色胶囊+声波条，松开后「正在转文字」，出字后消失
    capsule_px_per_cm = 75.3    # 主题胶囊按这个像素密度下的大小显示 (75.3 = 笔记本屏 2560px/34cm); 其他屏按其 EDID 物理宽度换算, 看着一样大. 0 = 不换算
    capsule_density_strength = 0.2   # 按像素密度缩放的力度: 0 = 不缩 (各屏按像素一样大), 1 = 完全按物理大小 (外接屏 0.57 倍, 太小); 0.4 (0.8 倍) 计时仍看不清 -> 0.2 (约 0.89 倍)
    capsule_theme = 'auto'      # 胶囊主题: 'auto' 跟随系统 / 'light' / 'dark' (托盘「胶囊主题」改, 存 user_state.json)
    recording_toast_margin = 24  # 胶囊与鼠标指针的间距 (像素; 2026-09-24 起胶囊跟鼠标, 原为距任务栏 16)
    recording_toast_opacity = 0.9  # 不透明度 0.2~1.0 (本地改: 0.88 -> 0.75 仿玻璃 -> 0.9 用户嫌不明显); 波形灵敏度按底噪自适应, 见 core/ui/toast_recording.py
    
    context = ''                # 兜底; 实际取安装目录 terms.txt (只对 Qwen3-ASR / Fun-ASR-Nano 生效)
    language = 'auto'           # 识别语言：'auto', 'chinese', 'english', 'japanese' 等（各引擎支持范围不同）

    trash_punc = '，。,.'       # 识别结果要消除的末尾标点
    trash_punc_thresh = 0       # 语义字数 <= 阈值时去除末尾标点; 0 = 不去 (本地改 2026-09-25: 口述主要进 Claude Code, 说完一句就该有标点)
    trash_punc_apps = ['WeiXin.exe', ]   # 对于指定的应用，强制去除末尾标点

    traditional_convert = False     # 是否将识别结果转换为繁体中文
    traditional_locale = 'zh-hant'  # 繁体地区：'zh-hant'（标准繁体）, 'zh-tw'（台湾繁体）, 'zh-hk'（香港繁体）

    hot = True                 # 是否启用热词替换（统一 RAG 匹配）
    hot_thresh = 0.85           # RAG 替换热词阈值（高阈值，用于实际替换）
    hot_similar = 0.6           # RAG 相似热词阈值（低阈值，用于 LLM 上下文）
    hot_rule = True             # 是否启用自定义规则替换（基于正则表达式）

    polish       = ''           # 二次整理服务商: '' = 关; 'deepseek' / 'minimax' (见 core/tools/polish_providers.py). 托盘「二次整理」改过后以 user_state.json 为准
    # 识别引擎 (本地改 2026-09-25): 流式 = 按住时气泡实时出字; 非流式 = 松开后整段上传; 在线失败自动用本地.
    # 托盘「识别」切换, 以 user_state.json 为准. 需要环境变量 DASHSCOPE_API_KEY. 费用按音频时长, 实测一天约 35 分钟音频, 几毛钱
    asr_engine = 'qwen-stream'  # 选项见 core/client/audio/cloud_asr.ENGINES: qwen-stream (流式) / qwen-batch / minimax-batch (非流式) / local
    asr_cloud_timeout = 1.5     # 流式: 松开后等最终结果的上限 (秒), 超时用本地识别
    asr_batch_timeout = 6.0     # 非流式: 松开后上传 + 识别的上限 (秒; 55s 录音实测 1.7~2.1s)
    asr_fallback = ['qwen-batch', 'step-batch', 'mimo-batch', 'minimax-batch', 'doubao-batch', 'zhipu-batch', 'local:qwen_asr']  # 候补顺序 (设置窗口拖拽): 非流式云端 key / 'local:<model_type>'; 走到第一个本地为止
    asr_hedge_sec = 2.0         # 主力这么久还没出结果就同时发给下一个候补 (每 10 秒音频再加 0.5 秒)
    polish_structure = False    # 结构化整理: 多件事排成编号列表并换行 (托盘「二次整理」里勾选; 需要先选服务商)

    llm_enabled = False         # 关: 要逐字原样; 且角色 enable_read_selection 会发 Ctrl+C, 终端里会中断
    llm_stop_key = 'esc'        # 中断 LLM 输出的快捷键

    enable_tray = True          # 客户端默认启用托盘图标功能
    mute_speaker_while_recording = True  # 按住录音时音箱静音, 松开恢复原状态 (防止播放声被收进去)
    mic_idle_release_sec = 0    # ⚠️ 保持 0 (麦克风常开). 2026-09-24 实测: 无线麦闲置后重开要 467ms, 期间键盘钩子被卡 471ms,
                                # 右 Alt 的按下漏进系统 -> 卡键. (Jabra 重开只要 20ms, 在家没暴露)
    follow_default_mic = True   # 自动跟随 Windows 默认录音设备 (切换/插拔后 2 秒内生效, 录音中不切)
    mic_auto = True             # 按 mic_priority 顺序自动用第一个有信号的麦克风 (设为 Windows 默认, 约 4 秒生效, 录音中不切);
                                # 无线麦发射器关着时录到全零, 会退到下一个. 关 = 只跟随手动选的默认设备. 设置页「麦克风」可开关
    mic_priority = ['Wireless Mic Rx', 'Jabra']  # 设备名 (片段即可); 设置页拖拽排序后存完整名称
    gpu_unboost_cmd = ''        # 同上根因, 客户端启动/退出时也不再调计划任务
    auto_start_server = True    # 客户端托管服务端: 只需启动客户端, 服务端隐藏运行, 退出时一并关闭

    # 日志配置
    log_level = 'DEBUG'         # 回滚排查用; 日志级别：'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'

    mic_seg_duration = 60       # 麦克风听写时分段长度：60秒
    mic_seg_overlap = 4         # 麦克风听写时分段重叠：4秒

    file_seg_duration = 60      # 转录文件时分段长度
    file_seg_overlap = 4        # 转录文件时分段重叠

    file_save_srt = True        # 转录文件时是否保存 srt 字幕
    file_save_txt = True        # 转录文件时是否保存 txt 文本（按标点切分后的）
    file_save_json = True       # 转录文件时是否保存 json 结果（含原始时间戳）
    file_save_merge = False      # 转录文件时是否保存 merge.txt（未切分的段落长文本）

    udp_broadcast = False               # 是否启用 UDP 广播输出结果
    udp_broadcast_targets = [           # UDP 广播目标地址列表，格式: (地址, 端口)
        ('127.255.255.255', 6017),      # 本地回环广播
        # ('192.168.1.255', 6017),      # 局域网广播（示例，按需启用）
    ]

    udp_control = False             # 是否启用 UDP 控制录音（外部程序发送 START/STOP 命令）
    udp_control_addr = '127.0.0.1'  # UDP 控制监听地址（'0.0.0.0' 允许外部访问）
    udp_control_port = 6018         # UDP 控制监听端口


# 快捷键配置说明
r"""
快捷键配置字段说明：
  key        - 按键名称（见下方可用按键列表）
  type       - 输入类型：'keyboard'（键盘）或 'mouse'（鼠标）
  suppress   - 是否阻塞按键（True=阻塞，False=不阻塞）
  hold_mode  - 长按模式（True=按下录音松开停止，False=单击开始再次单击停止）
  enabled    - 是否启用此快捷键

阻塞模式说明：
  - 阻塞模式  ：长按录音识别，短按（<0.3秒）则自动补发按键，不影响单击功能
  - 非阻塞模式：对于 CapsLock/NumLock/ScrollLock 这类切换键，松开时会自动补发，以恢复按键状态

可用按键名称：

  字母数字：a - z, 0 - 9（大键盘）

  符号键：, . / \ ` ' - = [ ] ; '


  功能键：f1 - f24

  控制键:
      ctrl_l,   ctrl_r,
      shift,  shift_r,
      alt_l,    alt_gr,
      cmd,    cmd_r

  特殊键：
      space, enter, tab, backspace, delete, insert, home, end
      page_up, page_down, esc, caps_lock, num_lock, scroll_lock
      print_screen, pause, menu

  方向键：up, down, left, right

  鼠标键：x1, x2

示例配置：
  {'key': 'caps_lock', 'type': 'keyboard', 'suppress': False, 'hold_mode': True, 'enabled': True}, 
  {'key': 'f12', 'type': 'keyboard', 'suppress': True, 'hold_mode': True, 'enabled': True}, 
  {'key': 'x2', 'type': 'mouse', 'suppress': True, 'hold_mode': True, 'enabled': True}, 
"""

