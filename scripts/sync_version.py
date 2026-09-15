"""构建打包时从 VERSION 文件同步版本号到暂存目录的 manifest 与 app/config.py

VERSION 是应用版本号的唯一真实来源；manifest 与 config.py 在仓库中可能滞后，
打包时以 VERSION 为准覆写暂存副本，确保 fpk 内版本号与 VERSION 一致。

用法: python scripts/sync_version.py <stage_dir>
"""
import re
import sys
from pathlib import Path


def main(stage_dir: str) -> None:
    version = Path("VERSION").read_text(encoding="utf-8").strip()
    if not version:
        print("错误：VERSION 文件为空", file=sys.stderr)
        sys.exit(1)

    stage = Path(stage_dir)

    # 1. manifest（平铺 INI，key=value）
    manifest = stage / "manifest"
    text = manifest.read_text(encoding="utf-8")
    text = re.sub(r"^version=.*$", f"version={version}", text, flags=re.MULTILINE)
    manifest.write_text(text, encoding="utf-8")

    # 2. app/config.py（APP_VERSION = "0.6.0"）
    config = stage / "app" / "app" / "config.py"
    text = config.read_text(encoding="utf-8")
    text = re.sub(
        r'^APP_VERSION\s*=\s*".*?"',
        f'APP_VERSION = "{version}"',
        text,
        flags=re.MULTILINE,
    )
    config.write_text(text, encoding="utf-8")

    print(f"==> 版本号同步：{version}（来源 VERSION 文件）")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: python scripts/sync_version.py <stage_dir>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
