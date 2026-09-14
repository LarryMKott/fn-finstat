"""生成日间 / 夜间两套应用图标

设计：**秤·币** —— 一枚铜金色硬币压在一道上升的刻度柱上。
      币 = 记账（价值计量），柱 = 增长趋势，二者叠加即「财务统计」。
      造型刻意保持简单几何：小尺寸（64px）下也不会糊成一团。

两套样式（同一造型，两套配色，保证辨识度一致）：
    日间版 light —— 深墨绿渐变底 + 米白刻度柱 + 铜金硬币
                    浅色背景（飞牛日间模式、白底桌面）下轮廓清晰、醒目
    夜间版 dark  —— 近黑墨绿底 + 低饱和刻度柱 + 提亮铜金硬币
                    深色背景下不刺眼，硬币提高明度但不过曝，对比度适宜

产出（统一改名为 light/dark，尺寸后缀不变）：
    frontend/public/icons/icon-light-{64,256}.png
    frontend/public/icons/icon-dark-{64,256}.png
    app/ui/images/icon_light_{64,256}.png     (飞牛桌面图标，须为其命名规约)
    app/ui/images/icon_dark_{64,256}.png
    ICON_LIGHT.PNG / ICON_DARK.PNG            (仓库根，与 manifest 同层)
    ICON_LIGHT_256.PNG / ICON_DARK_256.PNG

中性版（无主题后缀，同样用日间配色，保证宿主不识别 light/dark 字段时风格一致）：
    ICON.PNG / ICON_256.PNG                   (仓库根，fnpack 打包规范强制要求)
    app/ui/images/icon_{64,256}.png           (app/ui/config 的 icon 字段引用)

依赖 Pillow。任一装有 Pillow 的 Python 解释器均可运行：
    python scripts/make_icons.py

用法：
    python scripts/make_icons.py            # 生成全部两套
    python scripts/make_icons.py --preview  # 额外输出放大预览图便于肉眼检查

配套的适配验证图（尺寸/背景）由 scripts/preview_icons.py 生成。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent

# 超采样倍率：先按 4 倍绘制再缩小，得到干净的边缘（相当于免费抗锯齿）
SS = 4

# ---- 两套配色 ----
# 日间：深墨绿主色系。底足够深，配浅色桌面不漂浮；主体足够亮，小尺寸下可辨认
LIGHT = {
    "bg_top": (45, 84, 67),  # --pine-600
    "bg_bottom": (26, 51, 40),  # 更深一阶，形成纵向渐变
    "glow": (109, 163, 134),  # 顶部径向柔光（主色浅阶）
    "bar": (240, 244, 239),  # 米白刻度柱
    "coin_face": (203, 158, 84),  # 铜金硬币
    "coin_edge": (233, 197, 128),  # 硬币高光边
    "coin_mark": (45, 84, 67),  # 硬币上的刻痕（用底色镂空，保持干净）
    "shadow": (0, 0, 0, 56),
}

# 夜间：压低饱和度、抬高明度上限，深色背景下「柔和护眼」而非发亮刺眼
DARK = {
    "bg_top": (34, 50, 43),
    "bg_bottom": (14, 20, 18),
    "glow": (78, 118, 96),
    "bar": (186, 205, 192),  # 偏灰的浅绿，避免纯白在暗底上产生光晕
    "coin_face": (196, 158, 92),
    "coin_edge": (226, 197, 138),
    "coin_mark": (20, 28, 24),
    "shadow": (0, 0, 0, 90),
}


def _lerp(a: tuple[int, ...], b: tuple[int, ...], t: float) -> tuple[int, ...]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _rounded_mask(size: int, radius_ratio: float) -> Image.Image:
    """飞牛桌面图标为圆角方形（iOS 风格），圆角半径取边长的 22%"""
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=int(size * radius_ratio), fill=255
    )
    return mask


def _vertical_gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        row = _lerp(top, bottom, y / max(1, size - 1))
        for x in range(size):
            px[x, y] = row
    return img


def _radial_glow(
    size: int, color: tuple, strength: float, cy_ratio: float = 0.24
) -> tuple[Image.Image, Image.Image]:
    """顶部径向柔光：给纯渐变底添一点呼吸感，避免大色块显得平；返回 (柔光图层, 亮度蒙版)"""
    img = Image.new("L", (size, size), 0)
    px = img.load()
    cx, cy = size * 0.5, size * cy_ratio
    max_d = math.hypot(size * 0.5, size * cy_ratio) or 1
    for y in range(size):
        for x in range(size):
            d = math.hypot(x - cx, y - cy)
            v = max(0.0, 1.0 - d / max_d)
            px[x, y] = int(v * v * 255 * strength)
    layer = Image.new("RGB", (size, size), color)
    return layer, img


def _draw_mark(size: int, c: dict, dark: bool) -> Image.Image:
    """在透明层上绘制主体：三根刻度柱 + 一枚硬币"""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    S = size
    # 安全区：主体控制在 56% 边长的方形内，四周留出呼吸空间
    # （飞牛桌面图标会被系统加圆角遮罩，主体过大会在边缘被切）
    safe = S * 0.56
    y0 = (S - safe) / 2
    base_y = y0 + safe * 0.94  # 柱子基线略上移，给底部留喘息
    bar_w = safe * 0.175
    gap = safe * 0.10
    total_w = bar_w * 3 + gap * 2
    bar_x = (S - total_w) / 2

    # 三根柱子的高度比例：等差上升（0.40 / 0.62 / 0.86），读得出「增长」节奏
    heights = [0.40, 0.62, 0.86]
    radius = bar_w * 0.34
    for i, h in enumerate(heights):
        bx = bar_x + i * (bar_w + gap)
        bh = safe * h
        d.rounded_rectangle(
            (bx, base_y - bh, bx + bar_w, base_y),
            radius=radius,
            fill=c["bar"] + (255,),
        )

    # 硬币：抬到柱顶之上，只与中柱轻微交叠，保证 64px 下两者都读得清
    coin_r = safe * 0.265
    coin_cx = S / 2
    coin_cy = base_y - safe * 0.78
    # 投影：夜间模式加重，保证在深底上仍有分离度
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse(
        (
            coin_cx - coin_r,
            coin_cy - coin_r + safe * 0.045,
            coin_cx + coin_r,
            coin_cy + coin_r + safe * 0.045,
        ),
        fill=c["shadow"],
    )
    sh = sh.filter(ImageFilter.GaussianBlur(S * 0.018))
    layer = Image.alpha_composite(layer, sh)

    d = ImageDraw.Draw(layer)
    d.ellipse(
        (coin_cx - coin_r, coin_cy - coin_r, coin_cx + coin_r, coin_cy + coin_r),
        fill=c["coin_face"] + (255,),
    )
    # 内圈高光边：让硬币有金属厚度感，而不是一块死色圆饼
    ring = coin_r * 0.80
    d.ellipse(
        (coin_cx - ring, coin_cy - ring, coin_cx + ring, coin_cy + ring),
        outline=c["coin_edge"] + (255,),
        width=max(1, int(S * 0.016)),
    )
    # 中央竖刻痕：币的抽象符号，用底色镂空而非画线条，暗底下更干净
    notch_w = coin_r * 0.16
    notch_h = coin_r * 0.86
    d.rounded_rectangle(
        (
            coin_cx - notch_w / 2,
            coin_cy - notch_h / 2,
            coin_cx + notch_w / 2,
            coin_cy + notch_h / 2,
        ),
        radius=notch_w * 0.5,
        fill=c["coin_mark"] + (255,),
    )
    return layer


def render(size: int, c: dict, dark: bool, rounded: bool = True) -> Image.Image:
    """渲染单张图标：渐变底 + 柔光 + 主体，最后按圆角方形裁切"""
    big = size * SS
    bg = _vertical_gradient(big, c["bg_top"], c["bg_bottom"]).convert("RGBA")
    glow_rgb, glow_mask = _radial_glow(big, c["glow"], 0.34 if not dark else 0.30)
    bg = Image.composite(glow_rgb.convert("RGBA"), bg, glow_mask)
    bg = Image.alpha_composite(bg, _draw_mark(big, c, dark))

    if rounded:
        bg.putalpha(_rounded_mask(big, 0.22))
    return bg.resize((size, size), Image.LANCZOS)


# ---- 输出清单 ----
# (输出路径, 尺寸, 变体)  —— 飞牛桌面图标用下划线命名（icon_light_64.png），
# 符合其 ui/images 目录的既有习惯；Web 侧用连字符便于 manifest 引用
def targets() -> list[tuple[Path, int, str]]:
    out: list[tuple[Path, int, str]] = []
    pub = ROOT / "frontend" / "public" / "icons"
    ui = ROOT / "app" / "ui" / "images"
    for variant in ("light", "dark"):
        out += [
            (pub / f"icon-{variant}-64.png", 64, variant),
            (pub / f"icon-{variant}-256.png", 256, variant),
            (ui / f"icon_{variant}_64.png", 64, variant),
            (ui / f"icon_{variant}_256.png", 256, variant),
        ]
    for variant, cap in (("light", "LIGHT"), ("dark", "DARK")):
        out += [
            (ROOT / f"ICON_{cap}.PNG", 64, variant),
            (ROOT / f"ICON_{cap}_256.PNG", 256, variant),
        ]
    # 中性版：飞牛打包规范强制的根 ICON.PNG 与 ui/config 引用的 images/icon_{n}.png。
    # 这两个入口不感知主题，用日间配色生成，避免与 light/dark 版风格脱节
    out += [
        (ROOT / "ICON.PNG", 64, "neutral"),
        (ROOT / "ICON_256.PNG", 256, "neutral"),
        (ui / "icon_64.png", 64, "neutral"),
        (ui / "icon_256.png", 256, "neutral"),
    ]
    return out


def main() -> None:
    palette = {"light": (LIGHT, False), "dark": (DARK, True), "neutral": (LIGHT, False)}
    for path, size, variant in targets():
        c, is_dark = palette[variant]
        path.parent.mkdir(parents=True, exist_ok=True)
        img = render(size, c, is_dark)
        img.save(path, "PNG")
        print(f"generated: {path.relative_to(ROOT)} ({size}x{size}, {variant})")

    if "--preview" in sys.argv:
        # 并排放大预览：左日间右夜间，便于肉眼比对配色与轮廓
        scale = 2
        pad = 24
        cell = 256 * scale
        canvas = Image.new("RGB", (cell * 2 + pad * 3, cell + pad * 2), (128, 128, 128))
        for i, variant in enumerate(("light", "dark")):
            c, is_dark = palette[variant]
            big = render(256, c, is_dark).resize((cell, cell), Image.LANCZOS)
            canvas.paste(big, (pad + i * (cell + pad), pad), big)
        out = ROOT / "docs" / "images" / "图标双模式预览.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out, "PNG")
        print(f"preview: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
