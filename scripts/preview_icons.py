"""生成图标适配预览：小尺寸辨识度 + 双背景可见性

依赖先运行 scripts/make_icons.py 生成 frontend/public/icons 下的源图。

产出两张图（统一归档在 docs/images/）：
    docs/images/图标尺寸与背景适配预览.png —— 16/24/32/48/64/128px 下的实际观感，
                                              上排浅底看日间版，下排深底看夜间版
    docs/images/图标双模式对照.png          —— 两套图标 1:1 放大并排，看配色差异

用法：python scripts/preview_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
PUB = ROOT / "frontend" / "public" / "icons"
DOCS = ROOT / "docs" / "images"

# 浅色底（模拟飞牛日间模式桌面）与深色底（模拟夜间模式桌面）
LIGHT_BG = (245, 242, 236)
DARK_BG = (14, 18, 22)

# 预览图上的文字标签需要中文字形；PIL 默认位图字体没有，按顺序找一个可用的
FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",  # 微软雅黑
    "C:/Windows/Fonts/msyhl.ttc",
    "C:/Windows/Fonts/simhei.ttf",  # 黑体
    "C:/Windows/Fonts/simsun.ttc",  # 宋体
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def load_font(size: int) -> ImageFont.ImageFont:
    """取一个能显示中文的字体；都找不到时退回默认字体（标签会缺字，但图仍可出）"""
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def paste_center(
    canvas: Image.Image, icon: Image.Image, box: tuple[int, int, int, int]
) -> None:
    x0, y0, x1, y1 = box
    w, h = icon.size
    canvas.paste(icon, (x0 + (x1 - x0 - w) // 2, y0 + (y1 - y0 - h) // 2), icon)


def scaled(name: str, size: int) -> Image.Image:
    """按目标尺寸缩放；超过 64 的取 256 源图，避免从 64 放大糊掉"""
    src = PUB / f"icon-{name}-{256 if size > 64 else 64}.png"
    return Image.open(src).convert("RGBA").resize((size, size), Image.LANCZOS)


def build_sizes_board(scale: int = 2) -> Image.Image:
    sizes = [16, 24, 32, 48, 64, 128]
    cell = 148
    pad = 18
    row_h = cell + pad * 2
    width = pad + len(sizes) * (cell + pad)

    canvas = Image.new("RGB", (width, row_h * 2 + pad * 3), LIGHT_BG)
    draw = ImageDraw.Draw(canvas)

    # 上排：浅底 + 日间图标
    draw.rectangle((0, 0, width, row_h + pad), fill=LIGHT_BG)
    for i, s in enumerate(sizes):
        box = (pad + i * (cell + pad), pad, pad + i * (cell + pad) + cell, pad + cell)
        paste_center(canvas, scaled("light", s), box)

    # 下排：深底 + 夜间图标
    top = row_h + pad * 2
    draw.rectangle((0, top - pad, width, top + row_h), fill=DARK_BG)
    for i, s in enumerate(sizes):
        box = (pad + i * (cell + pad), top, pad + i * (cell + pad) + cell, top + cell)
        paste_center(canvas, scaled("dark", s), box)

    return canvas.resize((width * scale, (row_h * 2 + pad * 3) * scale), Image.LANCZOS)


def build_compare_board(scale: int = 2) -> Image.Image:
    """两套图标并排：同一造型、两套配色，验证辨识度一致"""
    size = 256
    pad = 24
    cell = size
    label_h = 40
    canvas = Image.new(
        "RGB", (cell * 2 + pad * 3, cell + pad * 2 + label_h), (110, 110, 110)
    )
    draw = ImageDraw.Draw(canvas)
    font = load_font(22)
    labels = {"light": "日间 LIGHT", "dark": "夜间 DARK"}
    for i, name in enumerate(("light", "dark")):
        box = (pad + i * (cell + pad), pad, pad + i * (cell + pad) + cell, pad + cell)
        paste_center(canvas, scaled(name, size), box)
        text = labels[name]
        # 居中：用 textlength 量宽，避免不同字体下标签偏左
        tw = draw.textlength(text, font=font)
        draw.text(
            ((box[0] + box[2]) / 2 - tw / 2, box[3] + 10),
            text,
            fill=(255, 255, 255),
            font=font,
        )
    return canvas.resize((canvas.width * scale, canvas.height * scale), Image.LANCZOS)


def main() -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    a = DOCS / "图标尺寸与背景适配预览.png"
    b = DOCS / "图标双模式对照.png"
    build_sizes_board().save(a)
    build_compare_board().save(b)
    print(f"saved: {a.relative_to(ROOT)}")
    print(f"saved: {b.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
