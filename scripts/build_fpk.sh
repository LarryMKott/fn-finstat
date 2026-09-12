#!/bin/bash
# 本地一键打包：单元测试门禁 -> 组装干净暂存目录 -> fnpack build -> 修正 Windows 权限位
# 用法：
#   bash scripts/build_fpk.sh                     # fnpack 在 PATH 中
#   FNPACK=/path/to/fnpack bash scripts/build_fpk.sh
#   PYTHON=/path/to/python bash scripts/build_fpk.sh   # 指定 Python（默认 python）
#   SKIP_TESTS=1 bash scripts/build_fpk.sh        # 跳过测试门禁（仅限本地调试，勿用于发布）
# 产物：项目根目录 fn-finstat.fpk
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="$ROOT/.local_tmp/fpk-stage"

# fnpack 解析顺序：FNPACK 环境变量 > PATH（fnpack / fnpack.exe）> 本地缓存 ~/.cache/fnpack
FNPACK="${FNPACK:-}"
if [ -z "$FNPACK" ]; then
  for cand in fnpack fnpack.exe "$HOME/.cache/fnpack/fnpack.exe"; do
    if command -v "$cand" >/dev/null 2>&1; then
      FNPACK="$cand"
      break
    fi
  done
fi
if [ -z "$FNPACK" ]; then
  echo "错误：未找到 fnpack，请安装到 PATH 或用 FNPACK=/path/to/fnpack 指定" >&2
  exit 1
fi

cd "$ROOT"

# 0. 单元测试门禁：所有测试通过才继续打包（ci_build.sh 已先行跑过时会继承 SKIP_TESTS=1）
#    PYTHON 未显式指定时不传递，由 run_tests.sh 自选解释器（项目 venv 优先于系统 python）
if [ "${SKIP_TESTS:-0}" = "1" ]; then
  echo "⚠️  SKIP_TESTS=1：已跳过单元测试门禁（仅限本地调试，勿用于发布）"
else
  echo "==> 打包前门禁：运行所有单元测试"
  if [ -n "${PYTHON:-}" ]; then
    PYTHON="$PYTHON" bash scripts/run_tests.sh
  else
    bash scripts/run_tests.sh
  fi
fi
PYTHON="${PYTHON:-python}"

# 1. 静态产物过期提醒：本脚本不执行前端构建，直接打包仓库内的 app/static。
#    frontend/src 存在晚于 app/static/index.html 的源文件时提示先构建。
#    仅警告不阻断：git clone/checkout 会刷新 mtime，可能误报；CI 由 ci_build.sh 先行构建不受影响。
STALE_FILE="$(find frontend/src -type f -newer app/static/index.html -print -quit 2>/dev/null)"
if [ -n "$STALE_FILE" ]; then
  echo "⚠️  警告：frontend/src 有晚于 app/static 的改动（${STALE_FILE}），静态产物可能已过期"
  echo "    建议先执行: cd frontend && npm run build，再重新打包"
fi

# 2. 组装暂存目录（只含打包必需文件；排除 venv/node_modules/本地数据/前端源码）
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

# 3. 打包（fnpack 会校验 manifest/config/图标/LICENSE/cmd 脚本）
"$FNPACK" build --directory "$STAGE"

# 4. Windows 版 fnpack 会把 cmd/ 权限位打成 0666，重写为 0755
"$PYTHON" scripts/fix_fpk_perm.py fn-finstat.fpk

# 5. 打包自检：比对源码与 app.tgz 的文件清单（零依赖，防漏打包文件），
#    并校验设备布局关键路径（requirements.txt、ui/ 在根，Python 包在 app/ 子目录）
#    逻辑在 scripts/fpk_selfcheck.py，与 build_fpk.bat 共用
"$PYTHON" scripts/fpk_selfcheck.py fn-finstat.fpk app

echo "打包完成: $ROOT/fn-finstat.fpk"
