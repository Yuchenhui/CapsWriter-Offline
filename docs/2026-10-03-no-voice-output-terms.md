# CapsWriter "没声音也输出全量术语" 修复

## 问题

用户麦克风没接收到声音(麦克风落外面 / 全静音),按右 Alt 后松开 → 系统**不应当输出任何文字**,但实际输出 347 字的整张术语词表(WSL/Debian/.../MiniMax/.../OpenSpec/会话。)。

### 根因链

1. 录音基本静音(典型 SNR 2-8 dB)
2. 主识别引擎 stepaudio-2.5-asr 返回空文字(0.38s 后),触发 fallback 链
3. fallback 走到 `core/client/audio/cloud_asr.py` 的 **qwen3-asr-flash** 分支 (line 757-769)
4. 该分支把 `load_terms()` 整段内容塞进 system message (line 760-762)
5. 喂空音频 + 整段术语词表 → qwen3-asr-flash 行为: **回显 system prompt 作为"识别结果"**
6. 没有任何"信噪比/说话时长"门槛判定 → 录音再空也照样送
7. text_output 也没拦截 → 348 字术语原文 paste 到活动窗口

### 历史背景

- 2026-09-23 实测触发过类似现象(见 `core/client/audio/recorder.py` line 199 注释),当时通过 `recorder.is_silence()` 拦截部分场景
- 2026-10-02 22:24:53 这次录到了完整事故链,SNR=8 dB,**刚好没被 is_silence() 拦截(SNR_MIN=8)**

## 修复思路

四条防线 + 一个警示 UI:

| 防线 | 位置 | 触发条件 |
|---|---|---|
| ① 极静音 (SNR < SNR_MIN=8) | `recorder.is_silence()` | recorder 判静 → 不送识别 → `warn_active('no voice detected')` |
| ② 边缘静音 (8 ≤ SNR < 12) | `result_processor._handle_message` | 拿到 is_final 结果后判 SNR < 12 → warn toast + return(不调 output) |
| ③ 警示 toast | `RecordingToast.warn` | 主题胶囊中央 4x 超采样琥珀圆 + ⚠ + pop() 缩放 + 3 秒自毁 |
| ④ 全链路 INFO log | 各层加 `[toast.warn]` 前缀 | `warn_active` → `RecordingToast.warn` → `update_text(warn:...)` → `_tick_frame mode 切换` → 首次叠画成功/失败 |

## 关键改动

### 1. `core/client/audio/recorder.py` line 213

```python
# 原: 静默关闭
from core.client.ui.recording_toast import close_active
close_active()

# 改: 触发警示
from core.client.ui.recording_toast import warn_active
warn_active('no voice detected')
```

### 2. `core/client/output/result_processor.py` line 211

在 `is_final` 处理开头加 SNR 兜底:

```python
_level = getattr(self.state, 'last_level', None) or {}
_snr = _level.get('snr') if isinstance(_level, dict) else None
if isinstance(_snr, (int, float)) and _snr < 12:
    warn_recording_hud('no voice detected')
    console.print('    [yellow]未接收到声音[/yellow]')
    return
```

阈值 12 是**硬编码**而不是用 `SNR_MIN`(SNR_MIN=8,因为 12 会误杀小声说话的历史教训,见 `core/client/audio/level.py:9`)

### 3. `core/client/ui/recording_toast.py`

新增模块级 `warn_active(text)` 函数 + 全链路 INFO log:

```python
def warn_active(text: str) -> None:
    logger.info(f'[toast.warn] warn_active 被调, text={text!r}, 活动实例={_active is not None}')
    with _active_lock:
        inst = _active
    if inst is not None:
        inst.warn(text)
```

### 4. `core/ui/toast_recording.py`

- `update_text` 加 `'warn:'` 前缀解析,设 `_mode='warn'`, `_proc_timeout_ms=3000`
- `_tick_frame` mode 切换加 `[toast.warn]` log
- `_themed_frame` / `_tick_classic` 末尾调 `_overlay_warn` / `_draw_warn_classic`
- 关键修复 `_themed_frame` 末尾 `theme_mode` mapping 保留 `'done': 'done'`(否则主题胶囊永远进不了 done 模式,finished() 永远 False → 永远不自毁)

```python
theme_mode = {'listening': 'recording', 'done': 'done'}.get(self._applied_mode, 'processing')
```

### 5. `core/ui/toast_recording.py` 新增 `_overlay_warn`

```python
def _overlay_warn(self, img):
    """琥珀色填充圆 + 中央 ⚠ (矢量: 竖条 + 圆点), 4x 超采样 + LANCZOS 缩,
    缩放曲线复用 capsule_themes.pop() (跟对号动画曲线一致)"""
    SS = 4
    D = 40   # 圆直径
    big = Image.new('RGBA', (D * SS, D * SS), (0, 0, 0, 0))
    dd = ImageDraw.Draw(big)
    dd.ellipse((0, 0, D * SS - 1, D * SS - 1), fill=(255, 196, 64, 255), outline=(184, 134, 11, 255), width=SS * 2)
    # 矢量 ⚠ (不用 Unicode 字符, 部分字体里没有)
    cx, cy = (D * SS) / 2, (D * SS) / 2
    bar_w = SS * 2
    dd.rectangle((cx - bar_w / 2, cy - D * SS * 0.2, cx + bar_w / 2, cy + D * SS * 0.15), fill=(38, 44, 56, 255))
    dd.ellipse((cx - bar_w * 1.5, cy + D * SS * 0.25, cx + bar_w * 1.5, cy + D * SS * 0.4), fill=(38, 44, 56, 255))
    small = big.resize((D, D), Image.LANCZOS)
    t = self._warn_t()
    from core.ui.capsule_themes import pop as _pop
    s, a = _pop(min(1.0, t / 0.45))
    sw, sh = max(1, int(D * s)), max(1, int(D * s))
    scaled = small.resize((sw, sh), Image.LANCZOS) if (sw, sh) != (D, D) else small
    layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    layer.paste(scaled, ((img.size[0] - sw) // 2, (img.size[1] - sh) // 2), scaled)
    return Image.alpha_composite(img, layer)
```

## 踩坑清单(下次不要再踩)

### 1. 字体用别名,PIL 不识别

`DEFAULT_FONT_FAMILY = 'Microsoft YaHei UI'` → PIL 在打包环境(无 fontconfig)里找不到 → `ImageFont.truetype('Microsoft YaHei UI', ...)` 报 `cannot open resource` → 每帧 50 次刷屏掩盖其他 log。

**正确**: 列常见真路径 `C:\Windows\Fonts\msyh.ttc` 等,找不到 fallback 到 `ImageFont.load_default()`。

### 2. PIL import 不全

```python
from PIL import ImageDraw, ImageFont   # 漏 Image
layer = Image.new('RGBA', ...)         # NameError
```

→ 异常抛在 `_tick` 路径 → toast 的 try/except 吞掉异常 → **胶囊永停帧卡死**。

**正确**: `from PIL import Image, ImageDraw, ImageFont`,并且 warn 路径单独 try/except 兜底(不能让 PIL 错误拖死整个 _tick)。

### 3. 主题胶囊 done 模式 mapping 错误

我之前把 `set_mode` 改成:
```python
theme_mode = 'recording' if self._applied_mode == 'listening' else 'processing'
```

→ 主题胶囊永远进不了 done 模式 → `finished(now)` 永远 False → **主题胶囊永远不自毁**(只在用户按右 Alt 短按时才被 `close_active()` 关掉)。

**正确**: 显式 mapping 保留 `'done': 'done'`:
```python
theme_mode = {'listening': 'recording', 'done': 'done'}.get(self._applied_mode, 'processing')
```

### 4. 录音被 recorder 拦截,result_processor 兜底永远不触发

recorder.is_silence() 拦截 → 不送识别 → result_processor 拿不到任何结果 → SNR 兜底永不生效 → 用户看到的"warn 静默关闭"。

**正确**: **recorder 的拦截路径也要触发警示**:
```python
warn_active('no voice detected')   # 而不是 close_active()
```

### 5. warn 走 done 路径,动画太短 (0.5 秒)

主题胶囊 done 动画 SHRINK+POP+HOLD+FADE ≈ 1.5 秒,加上 pop 只走 0.5 秒就播完自毁 → 用户看不到字。

**正确**: warn 走 processing 路径,主题胶囊画扫光 + 我们叠画 ⚠,设 `_proc_timeout_ms=3000` 一次性 after 自毁。

### 6. ⚠ Unicode 字符在某些字体里不存在

`ImageFont.truetype(...).textbbox('⚠', ...)` 可能返回 0 宽高 → 画出来啥都没有。

**正确**: 不用 Unicode 字符,直接画矢量(竖条 + 圆点)。即使字体缺失也不影响。

### 7. PowerShell Start-Process -RedirectStandardOutput 会让 conhost 卡死

```powershell
Start-Process conhost.exe --headless start_client.exe -RedirectStandardOutput log.txt  # 卡在托盘图标那一步
```

**正确**: 用 `wscript.exe start-hidden.vbs`(用户已经在用的启动脚本)→ 干净启动。

### 8. 没查 memory 是最大的错

`memory/ui-drawing-antialias.md` 已明确写:"圆角/圆/斜线/图标 → Pillow 4 倍画再缩小"。我没读 → 圆角毛刺 + ⚠ 模糊 → 用户骂三次。

**正确**: 动手前先 `memory` 查相关条目。

## 待解决(留给下一个工具 / 下次)

**敲桌声被识别成字**:敲桌是物理空气振动,SNR 满足"有说话"判定 → ASR 瞎猜输出乱文字。

候选方案:
- **A**: 抬高 SNR 阈值 12 → 18-20 (小声说话会被误杀)
- **B**: ASR 结果字数极少(≤5字)+ SNR 边缘 → 视为噪音丢弃 (极简指令被误杀)
- **C**: 识别结果"全是噪音词"判定 (需要建噪音词库)

用户当时说"你给我提示一下阈值,我来决定"。**没敲定就停了**。

## 复盘:这次会话浪费了多轮的原因

1. **第 1-3 轮**: 反复 import / set_mode mapping / 路径选错 → 没做 syntax/import 体检就直接当成功
2. **第 4-5 轮**: 把 try/except 当保险,实际 swallow 了真因 → 用户看不到问题
3. **第 6-7 轮**: 字体别名 / Pillow import 不全 → 应该**先跑一次烟雾测试**再走
4. **第 8 轮**: 警示框尺寸 200x44 太宽 → 应该**先看截图**(用户截图前我就该主动说"先看效果再交付")
5. **9 轮后**: 用户把要求降到"只要圆+感叹号",**早就该这样**

**教训**: 改动后**先看效果截图**, 再让用户测。
