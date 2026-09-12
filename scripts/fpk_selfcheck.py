"""FPK 打包自检：比对源码与 app.tgz 的文件清单（防漏打包文件），
并校验设备布局关键路径（requirements.txt、ui/ 在根，Python 包在 app/ 子目录）。

仅用标准库，build_fpk.sh 与 build_fpk.bat 共用。用法：
    python scripts/fpk_selfcheck.py [fpk路径] [源码app目录]
缺省：fpk=当前目录 fn-finstat.fpk，源码目录=当前目录 app/
"""

import io
import sys
import tarfile
from pathlib import Path

# 与打包脚本暂存清单保持一致：仅枚举入包的源码文件与目录（排除 venv/pycache 等）
PKG_FILES = ["main.py", "config.py"]
PKG_DIRS = ["api", "db", "parsers", "schemas", "services", "utils", "static", "ui"]


def source_paths(src_root: Path) -> set[str]:
    expected = {"requirements.txt"}  # 打包时置于 app.tgz 根目录
    for name in PKG_FILES:
        expected.add(f"app/{name}")
    for d in PKG_DIRS:
        for f in (src_root / d).rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                rel = f.relative_to(src_root).as_posix()
                # ui/ 打包时置于 tgz 根（桌面入口目录），其余在 app/ 子目录
                expected.add(rel if rel.startswith("ui/") else f"app/{rel}")
    return expected


def main() -> None:
    fpk = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("fn-finstat.fpk")
    src_root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("app")

    with tarfile.open(fpk, "r:gz") as outer:
        inner = outer.extractfile("app.tgz")
        assert inner is not None, "fpk 中缺少 app.tgz"
        with tarfile.open(fileobj=io.BytesIO(inner.read()), mode="r:gz") as app_tgz:
            packed = {n for n in app_tgz.getnames() if not n.endswith("/")}

    missing = source_paths(src_root) - packed
    assert not missing, f"漏打包文件: {sorted(missing)}"
    assert "requirements.txt" in packed, "requirements.txt 未在 app.tgz 根目录"
    assert "app/main.py" in packed, "app/main.py 未在 app.tgz 包目录"
    assert "ui/config" in packed, "ui/config 未在 app.tgz 中"
    print("打包自检通过：源码文件全部入包，设备布局正确")


if __name__ == "__main__":
    main()
