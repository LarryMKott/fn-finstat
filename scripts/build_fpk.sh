#!/bin/bash
# 本地一键打包：组装干净暂存目录 -> fnpack build -> 修正 Windows 权限位
# 用法：
#   bash scripts/build_fpk.sh                     # fnpack 在 PATH 中
#   FNPACK=/path/to/fnpack bash scripts/build_fpk.sh
#   PYTHON=/path/to/python bash scripts/build_fpk.sh   # 指定 Python（默认 python）
# 产物：项目根目录 fn-finstat.fpk
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FNPACK="${FNPACK:-fnpack}"
PYTHON="${PYTHON:-python}"
STAGE="$ROOT/.local_tmp/fpk-stage"

cd "$ROOT"

# 1. 组装暂存目录（只含打包必需文件；排除 venv/node_modules/本地数据/前端源码）
# 注意：fnpack 打包时会剥掉 app/ 一级前缀，平台将 app.tgz 解压到 ${TRIM_APPDEST} 根目录。
# 因此这里嵌套一层 app/app/：设备上得到 ${TRIM_APPDEST}/app/{main.py,api,...} 完整包结构，
# 以及平级的 ${TRIM_APPDEST}/requirements.txt 与 ${TRIM_APPDEST}/ui/。
rm -rf "$STAGE"
mkdir -p "$STAGE/app/app"
cp manifest ICON.PNG ICON_256.PNG LICENSE "$STAGE/"
cp -r config cmd wizard "$STAGE/"
cp app/main.py app/config.py "$STAGE/app/app/"
cp -r app/api app/db app/parsers app/schemas app/services app/utils app/static "$STAGE/app/app/"
cp app/requirements.txt "$STAGE/app/"
cp -r app/ui "$STAGE/app/"
find "$STAGE" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# 2. 打包（fnpack 会校验 manifest/config/图标/LICENSE/cmd 脚本）
"$FNPACK" build --directory "$STAGE"

# 3. Windows 版 fnpack 会把 cmd/ 权限位打成 0666，重写为 0755
"$PYTHON" scripts/fix_fpk_perm.py fn-finstat.fpk

# 4. 打包自检：比对源码与 app.tgz 的文件清单（零依赖，防漏打包文件），
#    并校验设备布局关键路径（requirements.txt、ui/ 在根，Python 包在 app/ 子目录）
"$PYTHON" - "$STAGE" <<'PYEOF'
import io, pathlib, tarfile

fpk = pathlib.Path("fn-finstat.fpk")
src_root = pathlib.Path("app")

def source_paths():
    # 与上方暂存清单保持一致：仅枚举入包的源码文件与目录（排除 venv/pycache 等）
    PKG_FILES = ["main.py", "config.py"]
    PKG_DIRS = ["api", "db", "parsers", "schemas", "services", "utils", "static", "ui"]
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

with tarfile.open(fpk, "r:gz") as outer:
    inner = outer.extractfile("app.tgz")
    with tarfile.open(fileobj=io.BytesIO(inner.read()), mode="r:gz") as app_tgz:
        packed = {n for n in app_tgz.getnames() if not n.endswith("/")}

missing = source_paths() - packed
assert not missing, f"漏打包文件: {sorted(missing)}"
assert "requirements.txt" in packed, "requirements.txt 未在 app.tgz 根目录"
assert "app/main.py" in packed, "app/main.py 未在 app.tgz 包目录"
assert "ui/config" in packed, "ui/config 未在 app.tgz 中"
print(f"打包自检通过：源码文件全部入包，设备布局正确")
PYEOF

echo "打包完成: $ROOT/fn-finstat.fpk"
