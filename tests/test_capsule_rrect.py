"""胶囊近圆时 rounded_rectangle 不再抛错 (Pillow 10.4 bug, 2026-09-28 打挂 Toast 线程).
运行: PYTHONIOENCODING=utf-8 uv run --no-project --python 3.13 --with pillow==10.4.0 python tests/test_capsule_rrect.py"""
import importlib.util
from pathlib import Path

from PIL import Image, ImageDraw

src = Path(__file__).resolve().parents[1] / 'core/ui/capsule_themes.py'
spec = importlib.util.spec_from_file_location('capsule_themes', src)
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)

raw_fail = 0
for H in (34, 44, 48, 52):                      # 各主题胶囊高度
    for k in (x / 10 for x in range(3, 31)):    # set_scale 允许的范围
        for w in range(H, H + 40):              # 缩成圆的过渡里宽度略大于高度
            for dx in (0, 0.5, -0.5, 1, 2):
                img = Image.new('L', (int((w + 100) * k), int((H + 100) * k)))
                x0 = (img.width - w * k) / 2
                box = (x0 + dx * k, 56 * k, x0 + (dx + w) * k, (56 + H) * k)
                try:
                    ImageDraw.Draw(img).rounded_rectangle(box, radius=H / 2 * k, fill=255)
                except ValueError:
                    raw_fail += 1
                ct.rrect(ImageDraw.Draw(img), box, H / 2 * k, fill=255)   # 不许抛
                assert img.getbbox(), (H, k, w, dx)                        # 且真的画出来了
print(f'rrect selftest ok (原生 rounded_rectangle 在这些尺寸下报错 {raw_fail} 次, 安全版 0 次)')
