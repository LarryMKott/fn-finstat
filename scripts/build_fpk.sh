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

# 4. 打包自检：按设备布局解包 app.tgz，验证 import app.main 可用（防漏打包文件）
"$PYTHON" - "$STAGE" <<'PYEOF'
import io, subprocess, sys, tarfile, tempfile, pathlib
fpk = pathlib.Path("fn-finstat.fpk")
with tarfile.open(fpk, "r:gz") as outer:
    inner = outer.extractfile("app.tgz")
    with tarfile.open(fileobj=io.BytesIO(inner.read()), mode="r:gz") as app_tgz:
        names = app_tgz.getnames()
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="fpk-check-"))
        try:
            app_tgz.extractall(tmp, filter="data")
        except TypeError:
            app_tgz.extractall(tmp)
assert "requirements.txt" in names, "requirements.txt 未在 app.tgz 根目录"
assert "app/main.py" in names, "app/main.py 未在 app.tgz 包目录"
r = subprocess.run([sys.executable, "-c", "import app.main"], cwd=tmp, capture_output=True, text=True)
if r.returncode != 0:
    sys.stderr.write(r.stderr)
    sys.exit("打包自检失败：设备布局下无法 import app.main")
print("打包自检通过：设备布局下 import app.main 成功")
PYEOF

echo "打包完成: $ROOT/fn-finstat.fpk"
