"""
逐像素透明的胶囊渲染 (UpdateLayeredWindow), 供 toast_recording 使用.

Tk 画布没有抗锯齿, 抠图色透明只能整像素透/不透, 整窗 alpha 又会把声波一起调淡.
这里每帧用 Pillow 画 RGBA (3x 超采样后缩小 = 抗锯齿), 直接贴到分层窗口:
半透明玻璃底 + 柔和投影 + 细亮边, 声波保持不透明. 初始化失败时调用方退回 Tk 画法.
"""
import ctypes
from ctypes import wintypes

# 本地改 2026-09-24: 按 Windows 应用主题 (AppsUseLightTheme) 取配色, 每个胶囊创建时读一次, 切主题下一句即生效.
# top/bottom = 玻璃底自上而下渐变 (RGBA, alpha 越小越透); rim = 外沿细边; shadow = 投影; dot = 转写中暗点
THEMES = {
    'dark':  dict(top=(62, 69, 86, 225), bottom=(38, 43, 56, 215), rim=(255, 255, 255, 70), shadow=(0, 0, 0, 110), dot='#5c6478'),
    'light': dict(top=(250, 251, 253, 235), bottom=(229, 233, 240, 225), rim=(0, 0, 0, 38), shadow=(0, 0, 0, 55), dot='#b3bbca'),
}


def theme() -> dict:
    try:   # 托盘「胶囊主题」手选 (config_client.capsule_theme: auto / light / dark), auto 才看系统
        from config_client import ClientConfig
        forced = getattr(ClientConfig, 'capsule_theme', 'auto')
        if forced in THEMES:
            return THEMES[forced]
    except Exception:
        pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as k:
            light = winreg.QueryValueEx(k, 'AppsUseLightTheme')[0] == 1
    except OSError:
        light = False
    return THEMES['light' if light else 'dark']


SHADOW_BLUR = 7                    # 投影模糊半径 (像素)
SHADOW_DY = 3                      # 投影下移 (像素)

_GWL_EXSTYLE = -20
_WS_EX = 0x00080000 | 0x00000020 | 0x00000080 | 0x08000000   # LAYERED | TRANSPARENT(点击穿透) | TOOLWINDOW | NOACTIVATE


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', ctypes.c_long), ('biHeight', ctypes.c_long),
                ('biPlanes', wintypes.WORD), ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', wintypes.DWORD), ('biClrImportant', wintypes.DWORD)]


class _BLEND(ctypes.Structure):
    _fields_ = [('op', ctypes.c_ubyte), ('flags', ctypes.c_ubyte), ('alpha', ctypes.c_ubyte), ('fmt', ctypes.c_ubyte)]


_u32 = ctypes.WinDLL('user32')
_g32 = ctypes.WinDLL('gdi32')
_u32.GetDC.restype = ctypes.c_void_p
_u32.GetDC.argtypes = [ctypes.c_void_p]
_u32.GetParent.restype = ctypes.c_void_p
_u32.GetParent.argtypes = [ctypes.c_void_p]
_u32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_u32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_u32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
_u32.UpdateLayeredWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.POINT),
                                     ctypes.POINTER(wintypes.SIZE), ctypes.c_void_p, ctypes.POINTER(wintypes.POINT),
                                     wintypes.DWORD, ctypes.POINTER(_BLEND), wintypes.DWORD]
_g32.CreateCompatibleDC.restype = ctypes.c_void_p
_g32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
_g32.CreateDIBSection.restype = ctypes.c_void_p
_g32.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                  ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wintypes.DWORD]
_g32.SelectObject.restype = ctypes.c_void_p
_g32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_g32.DeleteObject.argtypes = [ctypes.c_void_p]
_g32.DeleteDC.argtypes = [ctypes.c_void_p]


class LayeredRenderer:
    SS = 3        # 超采样倍数
    M = 16        # 窗口四周为投影预留的边距 (像素)

    @classmethod
    def create(cls, window, w, h, x, y):
        """w/h/x/y 是胶囊本身的尺寸与屏幕位置; 失败返回 None"""
        r = None
        try:
            r = cls(window, w, h, x, y)
            r.render([], 0.0)
            return r
        except Exception:
            if r is not None:
                r._free()
            return None

    def __init__(self, window, w, h, x, y):
        from PIL import Image, ImageChops, ImageDraw
        self.Image, self.ImageChops, self.ImageDraw = Image, ImageChops, ImageDraw

        window.update_idletasks()
        self.hwnd = _u32.GetParent(window.winfo_id()) or window.winfo_id()
        _u32.SetWindowLongW(self.hwnd, _GWL_EXSTYLE, _u32.GetWindowLongW(self.hwnd, _GWL_EXSTYLE) | _WS_EX)

        M = self.M
        self.pw, self.ph = w, h
        self.W, self.H = w + 2 * M, h + 2 * M
        self.pos = wintypes.POINT(x - M, y - M)
        self.size = wintypes.SIZE(self.W, self.H)
        self.base = self._background()

        # 32 位自顶向下 DIB, 建一次复用
        bmi = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), self.W, -self.H, 1, 32, 0, 0, 0, 0, 0, 0)
        self.hdc_screen = _u32.GetDC(None)
        self.hdc_mem = _g32.CreateCompatibleDC(self.hdc_screen)
        self.bits = ctypes.c_void_p()
        self.hbmp = _g32.CreateDIBSection(self.hdc_screen, ctypes.byref(bmi), 0, ctypes.byref(self.bits), None, 0)
        if not self.hbmp:
            self._free()
            raise OSError('CreateDIBSection 失败')
        _g32.SelectObject(self.hdc_mem, self.hbmp)
        window.bind('<Destroy>', lambda e: self._free(), add='+')

    def _background(self):
        """静态层: 投影 + 渐变玻璃底 + 细亮边 (SS 倍大小, RGBA). 顶部内高光在小胶囊上像多一道杠, 已去掉"""
        from PIL import ImageFilter
        Image, ImageDraw = self.Image, self.ImageDraw
        SS, M = self.SS, self.M
        W, H = self.W * SS, self.H * SS
        x1, y1 = M * SS, M * SS
        x2, y2 = (M + self.pw) * SS - 1, (M + self.ph) * SS - 1
        rad = self.ph * SS // 2
        pal = theme()

        shadow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle((x1, y1 + SHADOW_DY * SS, x2, y2 + SHADOW_DY * SS), rad, fill=pal['shadow'])
        img = shadow.filter(ImageFilter.GaussianBlur(SHADOW_BLUR * SS))

        # 渐变玻璃底: 1 像素宽的竖向渐变拉伸, 再用胶囊形状做蒙版
        n = y2 - y1
        grad = Image.new('RGBA', (1, n + 1))
        for j in range(n + 1):
            t = j / max(n, 1)
            grad.putpixel((0, j), tuple(round(a + (b - a) * t) for a, b in zip(pal['top'], pal['bottom'])))
        glass = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        glass.paste(grad.resize((x2 - x1 + 1, n + 1)), (x1, y1))
        mask = Image.new('L', (W, H), 0)
        ImageDraw.Draw(mask).rounded_rectangle((x1, y1, x2, y2), rad, fill=255)
        glass.putalpha(self.ImageChops.multiply(glass.getchannel('A'), mask))
        img = Image.alpha_composite(img, glass)

        d = ImageDraw.Draw(img)
        d.rounded_rectangle((x1, y1, x2, y2), rad, outline=pal['rim'], width=SS)
        return img

    @staticmethod
    def _mix(c, to, t):
        """'#rrggbb' 向 to (RGB 元组) 混合 t (0~1)"""
        rgb = (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))
        return tuple(round(a + (b - a) * t) for a, b in zip(rgb, to))

    def _cylinder(self, d, cx, top, bottom, r, c):
        """圆柱感: 两侧对称压暗的外圈 -> 居中原色主体 -> 略偏左的窄高光. 圆点 (top == bottom) 即成小球.
        2026-09-24 改: 原主体/高光都偏左, 右侧露出一道暗边, 浅色主题下像柱子后面有阴影"""
        for dx, k, col in ((0.0, 1.0, self._mix(c, (0, 0, 0), 0.22)),
                           (0.0, 0.66, self._mix(c, (0, 0, 0), 0.0)),
                           (-0.08, 0.26, self._mix(c, (255, 255, 255), 0.5))):
            rr = r * k
            x = cx + dx * r * 2
            d.rounded_rectangle((x - rr, top - rr, x + rr, bottom + rr), radius=rr, fill=col)

    def render(self, prims, alpha):
        """画一帧. prims = [(x1, y1, x2, y2, 线宽, '#rrggbb')] 胶囊局部坐标的圆头线段; alpha = 整窗淡入 0~1"""
        SS, M = self.SS, self.M
        img = self.base.copy()
        d = self.ImageDraw.Draw(img)
        for x1, y1, x2, y2, w, c in prims:
            p1 = ((x1 + M) * SS, (y1 + M) * SS)
            p2 = ((x2 + M) * SS, (y2 + M) * SS)
            r = w * SS / 2
            if x1 == x2:   # 本地改 2026-09-24: 竖条/圆点画成对称圆角矩形 + 圆柱明暗 (原 直线+两端补圆, 偶数线宽时线身偏半像素, 一侧像缺口)
                self._cylinder(d, p1[0], min(p1[1], p2[1]), max(p1[1], p2[1]), r, c)
                continue
            d.line((p1, p2), fill=c, width=max(1, round(w * SS)))
            for px, py in (p1, p2):   # 圆头
                d.ellipse((px - r, py - r, px + r, py + r), fill=c)
        # 本地改: 先预乘再 reduce (整数倍盒式缩小): LANCZOS 1.15ms -> reduce ~0.4ms/帧 (实测整帧 1.59 -> 0.57ms),
        # 且预乘后再缩小, 边缘不会把透明像素的黑色平均进来. UpdateLayeredWindow 正好要预乘 alpha 的 BGRA
        data = img.convert('RGBa').reduce(SS).tobytes('raw', 'BGRa')
        ctypes.memmove(self.bits, data, len(data))

        src = wintypes.POINT(0, 0)
        blend = _BLEND(0, 0, max(0, min(255, round(alpha * 255))), 1)   # AC_SRC_OVER, AC_SRC_ALPHA
        _u32.UpdateLayeredWindow(self.hwnd, self.hdc_screen, ctypes.byref(self.pos), ctypes.byref(self.size),
                                 self.hdc_mem, ctypes.byref(src), 0, ctypes.byref(blend), 2)   # ULW_ALPHA

    def _free(self):
        if getattr(self, 'hbmp', None):
            _g32.DeleteObject(self.hbmp)
            self.hbmp = None
        if getattr(self, 'hdc_mem', None):
            _g32.DeleteDC(self.hdc_mem)
            self.hdc_mem = None
        if getattr(self, 'hdc_screen', None):
            _u32.ReleaseDC(None, self.hdc_screen)
            self.hdc_screen = None
