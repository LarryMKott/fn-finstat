#!/bin/bash
# 本地一键打包：单元测试门禁 -> 组装干净暂存目录 -> fnpack build -> 修正 Windows 权限位
# 用法：
#   bash scripts/build_fpk.sh                     # fnpack 在 PATH 中
#   FNPACK=/path/to/fnpack bash scripts/build_fpk.sh
#   PYTHON=/path/to/python bash scripts/build_fpk.sh   # 指定 Python（默认 python）
#   SKIP_TESTS=1 bash scripts/build_fpk.sh        # 跳过测试门禁（仅限本地调试，勿用于发布）
#   BUILD_CHANNEL=dev bash scripts/build_fpk.sh   # 打测试版（版本号带 -dev 后缀）
# CI 会额外注入（已设置时直接沿用，不再重算）：BUILD_VERSION、CHANNEL_ALIAS、
# BUILD_NUMBER、SHORT_SHA。
# 产物（项目根目录）：
#   fn-finstat.fpk                fnpack 原始输出（仅作流水线制品，不上传 Release 附件）
#   fn-finstat-{渠道别名}.fpk     稳定下载入口：release → latest，dev → dev
#   fn-finstat-v{版本}.fpk        带版本号副本，用于区分具体是哪一次构建
#   MD5SUMS.txt                   上面两个交付产物的 MD5 校验文件（md5sum -c 可用）
#
# 构建渠道（BUILD_CHANNEL）：
#   release（默认）产物版本号 = VERSION 原值，如 0.7.1，别名为 latest
#   dev            产物版本号 = VERSION + 预发布段，如 0.7.1-dev.42.g1a2b3c4，别名为 dev
#   版本号会写进包内 manifest 与 app/config.py，「关于」页与设备应用列表随之
#   显示测试版标识；frontend/package.json 始终跟随 VERSION 原值，不写派生版本。
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# STAGE 刻意用相对路径，而不是 "$ROOT/.local_tmp/fpk-stage"：
# 在 Git Bash（MSYS）下 $ROOT 形如 /d/pj/fn-finstat，把它交给原生 Windows 程序
# （python.exe / fnpack.exe）会被当成 drive-relative 路径解析成 \d\pj\...，
# sync_version.py 随即报 FileNotFoundError。脚本下面已 cd "$ROOT"，
# 相对路径同样落在仓库根，且在 Linux CI 与 Git Bash 下行为一致。
STAGE=".local_tmp/fpk-stage"

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

# 0.5 产物版本号：BUILD_VERSION 显式指定优先（CI 用它保证与产物命名同源），
#     否则按渠道从 VERSION 派生。渠道写错时 sync_version.py 返回非零，
#     set -e 会在这里直接中止——避免"打错渠道却产出正式版包"。
BUILD_CHANNEL="${BUILD_CHANNEL:-release}"
if [ -z "${BUILD_VERSION:-}" ]; then
  BUILD_NUMBER="${BUILD_NUMBER:-}"
  SHORT_SHA="${SHORT_SHA:-$(git rev-parse --short=7 HEAD 2>/dev/null || true)}"
  BUILD_VERSION="$("$PYTHON" scripts/sync_version.py --print \
    --channel "$BUILD_CHANNEL" --build-number "$BUILD_NUMBER" --short-sha "$SHORT_SHA")"
fi
if [ -z "$BUILD_VERSION" ]; then
  echo "错误：无法确定产物版本号（渠道 $BUILD_CHANNEL）" >&2
  exit 1
fi
echo "==> 构建渠道：${BUILD_CHANNEL} · 产物版本：${BUILD_VERSION}"

# 渠道别名同样由 sync_version.py 派生（latest / dev），不要在脚本里硬编字符串——
# 渠道词散落在多个脚本里最容易出现拼写漂移（latest / release / stable 各写一半）。
# 已设置时直接沿用（CI 传进来，保证与产物命名同源）。
if [ -z "${CHANNEL_ALIAS:-}" ]; then
  CHANNEL_ALIAS="$("$PYTHON" scripts/sync_version.py --print-alias \
    --channel "$BUILD_CHANNEL")"
fi
if [ -z "$CHANNEL_ALIAS" ]; then
  echo "错误：无法确定渠道别名（渠道 $BUILD_CHANNEL）" >&2
  exit 1
fi
echo "==> 产物别名：fn-finstat-${CHANNEL_ALIAS}.fpk"

# 1. 静态产物门禁：app/static 整个目录不入库（.gitignore 已排除），
#    新 clone 的仓库里没有产物，直接打包会得到引用了不存在 JS/CSS 的残缺 FPK（用户端白屏）。
#    因此这里做硬校验：产物缺失即中止并给出构建指引，不再只是过期警告。
#    入口文件单独先查：只查 assets/ 的话，index.html 缺失会在后面的引用校验里
#    报成"找不到入口文件"，看不出该执行哪条构建命令。
#    CI（scripts/ci_build.sh）会先执行前端构建，故不受影响。
if [ ! -f "app/static/index.html" ]; then
  echo "错误：前端构建产物缺失（app/static/index.html 不存在）" >&2
  echo "      请先构建前端：cd frontend && npm ci && npm run build" >&2
  echo "      提示：CI 由 scripts/ci_build.sh 自动完成此步骤" >&2
  exit 1
fi
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
cp manifest LICENSE "$STAGE/"
cp assets/icons/ICON.PNG assets/icons/ICON_256.PNG "$STAGE/"
cp -r config cmd wizard "$STAGE/"
# 顶层 .py 模块全量入包（勿改回显式清单：曾因只列 main/config，新增顶层模块
# file_settings.py 漏打包，设备上 import 即 ModuleNotFoundError 启动失败；
# 自检 fpk_selfcheck.py 按同一 glob 规则动态推导期望清单，两边保持联动）
cp app/*.py "$STAGE/app/app/"
cp -r app/api app/core app/db app/parsers app/schemas app/services app/utils app/static "$STAGE/app/app/"
cp app/requirements.txt "$STAGE/app/"
cp -r app/ui "$STAGE/app/"
find "$STAGE" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
# .gitkeep 只是为了让空目录能入库，不应出现在设备的 /var/apps/<appname>/wizard/ 里
rm -f "$STAGE/wizard/.gitkeep"

# 2.5 把产物版本号写进暂存目录的 manifest 与 app/config.py
#     VERSION 是版本号的唯一真实来源；manifest 与 config.py 在仓库中可能滞后，
#     打包时以 VERSION 为准覆写暂存副本，确保 fpk 内版本号与 VERSION 一致。
#     dev 渠道下这里是带 -dev 后缀的派生版本，包内在设备上即显示为测试版。
#     --channel 一并传入：版本号已由 --version 给定，这里的渠道只影响日志文案，
#     不传的话日志会把 dev 构建显示成 release，排查时极易误判。
"$PYTHON" scripts/sync_version.py "$STAGE" \
  --version "$BUILD_VERSION" --channel "$BUILD_CHANNEL"

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

# 7. 产物命名：渠道别名（稳定下载入口）+ 带版本号副本（可追溯具体构建）
#    别名同名覆盖，因此 fn-finstat-latest.fpk / fn-finstat-dev.fpk 的下载链接
#    可以长期不变；版本号副本回答"这是哪一次构建"。
FPK_ALIAS="fn-finstat-${CHANNEL_ALIAS}.fpk"
FPK_VERSIONED="fn-finstat-v${BUILD_VERSION}.fpk"
cp fn-finstat.fpk "$FPK_ALIAS"
cp fn-finstat.fpk "$FPK_VERSIONED"

# 8. MD5 校验文件：只覆盖交付出去的两个产物——裸名不上传（见第 7 步注释），
#    把它列进去只会让用户困惑"为什么校验文件里有个我下载不到的包"。
#    先删旧文件：构建若中断在生成之前，残留的上一轮 MD5SUMS.txt 会被当成本次产物
#    （与 fnpack 先删旧 fpk、releaseNode.txt 先删旧文件是同一类防护）。
rm -f MD5SUMS.txt
"$PYTHON" scripts/gen_checksums.py -o MD5SUMS.txt "$FPK_ALIAS" "$FPK_VERSIONED"

echo "打包完成: $ROOT/fn-finstat.fpk"
echo "渠道别名:     $ROOT/$FPK_ALIAS"
echo "带版本号副本: $ROOT/$FPK_VERSIONED"
echo "MD5 校验文件: $ROOT/MD5SUMS.txt"
if [ "$BUILD_CHANNEL" = "dev" ]; then
  echo "⚠️  这是测试版本（${BUILD_VERSION}），请勿作为正式发布产物分发"
fi
