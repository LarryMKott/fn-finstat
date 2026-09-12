#!/bin/bash
# CI 自包含构建脚本：单元测试门禁 -> 前端构建 -> 下载 fnpack -> 打包 FPK（含权限修复与设备布局自检）
# 适用于 Gitee Go（build@nodejs 云端编译）或其他 Linux CI 环境。
# 可用环境变量：
#   NODE_VERSION   自举的 Node 版本（默认 20.19.0，基础镜像 Node >= 18 时跳过自举）
#   FNPACK_VERSION fnpack 版本（默认 1.2.3）
#   NPM_REGISTRY   npm 镜像源（默认 npmmirror，国内加速）
#   PIP_INDEX_URL  pip 镜像源（默认清华 TUNA，国内加速）
#   SKIP_TESTS     设为 1 跳过单元测试门禁（仅限紧急调试，勿用于发布）
set -e
cd "$(dirname "$0")/.."

NODE_VERSION="${NODE_VERSION:-20.19.0}"
FNPACK_VERSION="${FNPACK_VERSION:-1.2.3}"
FNPACK_URL="${FNPACK_URL:-https://static2.fnnas.com/fnpack/fnpack-${FNPACK_VERSION}-linux-amd64}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"

# 1. python3 + pip：单元测试门禁与打包自检（fix_fpk_perm.py）都依赖
if ! command -v python3 >/dev/null 2>&1; then
  echo "==> python3 缺失，尝试安装"
  apt-get update -qq && apt-get install -y -qq python3 \
    || yum install -y -q python3 \
    || { echo "错误：无法安装 python3，无法执行测试门禁与打包自检"; exit 1; }
fi
if ! python3 -m pip --version >/dev/null 2>&1; then
  echo "==> pip 缺失，尝试安装"
  apt-get update -qq && apt-get install -y -qq python3-pip \
    || yum install -y -q python3-pip \
    || { echo "错误：无法安装 pip，无法执行测试门禁"; exit 1; }
fi

# 2. 单元测试门禁：所有测试通过才继续构建（放在前端构建前，失败时不必白跑 npm ci）。
#    通过后导出 SKIP_TESTS=1，避免 build_fpk.sh 末尾重复执行同一套测试。
if [ "${SKIP_TESTS:-0}" = "1" ]; then
  echo "⚠️  SKIP_TESTS=1：已跳过单元测试门禁（仅限紧急调试，勿用于发布）"
else
  echo "==> 构建前门禁：运行所有单元测试"
  PYTHON="$(command -v python3)" bash scripts/run_tests.sh
  export SKIP_TESTS=1
fi

# 3. Node 自举：基础镜像自带 Node < 18（无法运行 Vite 5）时，下载便携版 Node 20
need_node=1
if command -v node >/dev/null 2>&1; then
  major="$(node -v | sed 's/^v\([0-9]*\).*/\1/')"
  [ "${major:-0}" -ge 18 ] && need_node=0
fi
if [ "$need_node" = 1 ]; then
  echo "==> bootstrap Node v${NODE_VERSION}"
  mkdir -p /tmp/node20
  curl -fsSL "https://npmmirror.com/mirrors/node/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.gz" \
    | tar -xz -C /tmp/node20 --strip-components=1
  export PATH="/tmp/node20/bin:$PATH"
fi
node -v
npm -v

# 4. 前端构建（产物输出至 app/static）
echo "==> frontend build"
cd frontend
npm config set registry "$NPM_REGISTRY"
npm ci --no-fund --no-audit || npm install --no-fund --no-audit
npm run build
cd ..

# 5. 下载 fnpack（Linux 版）
FNPACK_BIN="${HOME}/.cache/fnpack/fnpack"
mkdir -p "$(dirname "$FNPACK_BIN")"
if [ ! -x "$FNPACK_BIN" ]; then
  echo "==> download fnpack v${FNPACK_VERSION}"
  curl -fsSL "$FNPACK_URL" -o "$FNPACK_BIN"
  chmod +x "$FNPACK_BIN"
fi

# 6. 打包（含 cmd/ 权限位修复与设备布局自检；测试门禁见步骤 2）
echo "==> fnpack build"
PYTHON="$(command -v python3)" FNPACK="$FNPACK_BIN" bash scripts/build_fpk.sh

echo "==> 构建产物：$(pwd)/fn-finstat.fpk"
sha256sum fn-finstat.fpk
