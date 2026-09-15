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

# 1. 静态产物门禁：app/static/assets 不入库（.gitignore 已排除），
#    新 clone 的仓库里没有产物，直接打包会得到引用了不存在 JS/CSS 的残缺 FPK（用户端白屏）。
#    因此这里做硬校验：产物缺失即中止并给出构建指引，不再只是过期警告。
#    CI（scripts/ci_build.sh）会先执行前端构建，故不受影响。
if [ ! -d "app/static/assets" ] || [ -z "$(ls -A app/static/assets 2>/dev/null)" ]; then
  echo "错误：前端构建产物缺失（app/static/assets 为空）" >&2
  echo "      请先构建前端：cd frontend && npm ci && npm run build" >&2
  echo "      提示：CI 由 scripts/ci_build.sh 自动完成此步骤" >&2
  exit 1
fi

#    产物过期提醒：frontend/src 存在晚于 app/static/index.html 的源文件时提示先构建。
#    仅警告不阻断：git clone/checkout 会刷新 mtime，可能误报；CI 由 ci_build.sh 先行构建不受影响。
STALE_FILE="$(find frontend/src -type f -newer app/static/index.html -print -quit 2>/dev/null)"
if [ -n "$STALE_FILE" ]; then
  echo "⚠️  警告：frontend/src 有晚于 app/static 的改动（${STALE_FILE}），静态产物可能已过期"
  echo "    建议先执行: cd frontend && npm run build，再重新打包"
fi

#    引用校验：index.html 引用的每个 assets/* 必须真实存在（防白屏，见评审报告 S-1/L-1）
"${PYTHON:-python}" scripts/check_assets_refs.py app/static/index.html app/static

# 2. 组装暂存目录（只含打包必需文件；排除 venv/node_modules/本地数据/前端源码）
# 注意：fnpack 打包时会剥掉 app/ 一级前缀，平台将 app.tgz 解压到 ${TRIM_APPDEST} 根目录。
# 因此这里嵌套一层 app/app/：设备上得到 ${TRIM_APPDEST}/app/{main.py,api,...} 完整包结构，
# 以及平级的 ${TRIM_APPDEST}/requirements.txt 与 ${TRIM_APPDEST}/ui/。
rm -rf "$STAGE"
mkdir -p "$STAGE/app/app"
cp manifest ICON.PNG ICON_256.PNG LICENSE "$STAGE/"
cp -r config cmd wizard "$STAGE/"
cp app/main.py app/config.py "$STAGE/app/app/"
cp -r app/api app/core app/db app/parsers app/schemas app/services app/utils app/static "$STAGE/app/app/"
cp app/requirements.txt "$STAGE/app/"
cp -r app/ui "$STAGE/app/"
find "$STAGE" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
# .gitkeep 只是为了让空目录能入库，不应出现在设备的 /var/apps/<appname>/wizard/ 里
rm -f "$STAGE/wizard/.gitkeep"

# 3. 打包（fnpack 会校验 manifest/config/图标/LICENSE/cmd 脚本与 wizard JSON）
#    坑：fnpack 校验失败时**退出码仍然是 0**，只在 stdout 打印 "Packing failed"。
#    只靠 set -e 会静默放过，后续步骤会把上一次的旧 FPK 当成本次产物。
#    因此先删除旧产物，再用「输出关键字 + 产物是否生成」双重判定。
rm -f fn-finstat.fpk
FNPACK_OUT="$("$FNPACK" build --directory "$STAGE" 2>&1)"
FNPACK_RC=$?
printf '%s\n' "$FNPACK_OUT"
# 双重判定：fnpack 校验失败时退出码可能仍是 0（历史坑），故关键字与退出码任一
# 命中即失败；两者都放过还有「产物是否存在」+ fpk_selfcheck 兜底。
FNPACK_FAILED=0
[ "$FNPACK_RC" -ne 0 ] && FNPACK_FAILED=1
case "$FNPACK_OUT" in
  *"Packing failed"*) FNPACK_FAILED=1 ;;
esac
if [ "$FNPACK_FAILED" -ne 0 ]; then
  echo "错误：fnpack 打包失败（退出码 $FNPACK_RC，见上方输出），已删除旧产物以免误用" >&2
  exit 1
fi
if [ ! -f fn-finstat.fpk ]; then
  echo "错误：fnpack 未生成产物 fn-finstat.fpk" >&2
  exit 1
fi

# 4. Windows 版 fnpack 会把 cmd/ 权限位打成 0666，重写为 0755
"$PYTHON" scripts/fix_fpk_perm.py fn-finstat.fpk

# 5. 打包自检：比对源码与 app.tgz 的文件清单（零依赖，防漏打包文件），
#    并校验设备布局关键路径（requirements.txt、ui/ 在根，Python 包在 app/ 子目录）
#    逻辑在 scripts/fpk_selfcheck.py，与 build_fpk.bat 共用
"$PYTHON" scripts/fpk_selfcheck.py fn-finstat.fpk app

# 6. 清理打包暂存目录（纯构建中间产物；打包失败时不走到这里，保留现场便于排查。
#    不及时清理会被工作树级安全扫描当作源码反复误报，见 .gitignore 的 .local_tmp/ 注释）
rm -rf "$STAGE"

echo "打包完成: $ROOT/fn-finstat.fpk"
