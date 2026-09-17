"""构建打包时从 VERSION 文件同步版本号到暂存目录的 manifest 与 app/config.py

VERSION 是应用版本号的唯一真实来源；manifest 与 config.py 在仓库中可能滞后，
打包时以 VERSION 为准覆写暂存副本，确保 fpk 内版本号与 VERSION 一致。

另外提供前端 package.json 的版本同步与全仓库一致性校验：
frontend/package.json 的 version 同样跟随 VERSION，避免仓库里出现两个
互相矛盾的版本号（它不在打包暂存目录内，需单独处理）。

用法:
  python scripts/sync_version.py <stage_dir>          # 打包时同步暂存目录 + 前端
  python scripts/sync_version.py --check              # 仅校验全仓库版本号是否一致
"""

import json
import re
import sys
from pathlib import Path

VERSION_FILE = Path("VERSION")
FRONTEND_PKG = Path("frontend") / "package.json"


def read_version() -> str:
    """读取 VERSION 文件，为空时终止（它是唯一的版本来源，不能缺）。"""
    if not VERSION_FILE.exists():
        print("错误：找不到 VERSION 文件", file=sys.stderr)
        sys.exit(1)
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not version:
        print("错误：VERSION 文件为空", file=sys.stderr)
        sys.exit(1)
    return version


def sync_stage(stage: Path, version: str) -> None:
    """把版本号覆写进打包暂存目录的 manifest 与 app/config.py。"""
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


def sync_frontend_package(version: str) -> None:
    """把版本号写回源码树中的 frontend/package.json。

    注意这里改的是**源码树**而非暂存目录——前端源码不进 fpk，但版本号必须与
    VERSION 保持一致，否则仓库里会出现两个互相矛盾的应用版本号。
    """
    if not FRONTEND_PKG.exists():
        return
    raw = FRONTEND_PKG.read_text(encoding="utf-8")
    data = json.loads(raw)
    if data.get("version") == version:
        return
    old = data.get("version")
    data["version"] = version
    FRONTEND_PKG.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"==> 前端 package.json 版本号：{old} → {version}")


def check() -> int:
    """校验全仓库版本号一致（用于发版前自检）。

    返回 0 = 一致；1 = 存在漂移。manifest / config.py 允许滞后（打包时会覆写），
    因此只检查 package.json 这个"会与源码一起被人看到"的副本。
    """
    version = read_version()
    ok = True

    if FRONTEND_PKG.exists():
        pkg_version = json.loads(FRONTEND_PKG.read_text(encoding="utf-8")).get(
            "version"
        )
        if pkg_version != version:
            print(
                f"❌ 版本号漂移：VERSION = {version}，"
                f"frontend/package.json = {pkg_version}",
                file=sys.stderr,
            )
            print(
                "   修复：python scripts/sync_version.py --sync-frontend",
                file=sys.stderr,
            )
            ok = False
        else:
            print(f"✅ frontend/package.json 版本号一致：{version}")

    return 0 if ok else 1


def main(argv: list[str]) -> int:
    # 仅校验模式
    if "--check" in argv:
        return check()

    # 仅同步前端模式（发版时用，不需要 stage_dir）
    if "--sync-frontend" in argv:
        version = read_version()
        sync_frontend_package(version)
        print(f"==> 版本号同步：{version}（来源 VERSION 文件）")
        return 0

    # 打包模式：同步暂存目录 + 前端
    # argv 已去掉脚本名（main 接收 sys.argv[1:]），单个 stage_dir 参数时 len == 1
    if len(argv) != 1:
        print(
            "用法:\n"
            "  python scripts/sync_version.py <stage_dir>       # 打包时同步\n"
            "  python scripts/sync_version.py --check           # 校验一致性\n"
            "  python scripts/sync_version.py --sync-frontend   # 仅同步前端",
            file=sys.stderr,
        )
        return 1

    version = read_version()
    sync_stage(Path(argv[0]), version)
    sync_frontend_package(version)
    print(f"==> 版本号同步：{version}（来源 VERSION 文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
