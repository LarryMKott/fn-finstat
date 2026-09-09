"""生成应用图标：ICON.PNG / ICON_256.PNG 与 app/ui/images/icon_64.png / icon_256.png

用法：
    python scripts/make_icons.py <源图路径>
"""
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent

TARGETS = {
    ROOT / "ICON.PNG": 64,
    ROOT / "ICON_256.PNG": 256,
    ROOT / "app" / "ui" / "images" / "icon_64.png": 64,
    ROOT / "app" / "ui" / "images" / "icon_256.png": 256,
}


def main(src: str) -> None:
    img = Image.open(src).convert("RGBA")
    for path, size in TARGETS.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        img.resize((size, size), Image.LANCZOS).save(path, "PNG")
        print(f"generated: {path} ({size}x{size})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
