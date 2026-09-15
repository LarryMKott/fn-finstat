#!/bin/bash
# CI 构建打包：Node 自举 + 前端构建 + fnpack 打包 + 产物重命名
# 要求先运行 ci_env.sh 确保 python3/pip 已就绪
# 可用环境变量：
#   NODE_VERSION   Node 版本（默认 20.19.0）
#   FNPACK_VERSION  fnpack 版本（默认 1.2.3）
#   NPM_REGISTRY    npm 镜像源（默认 npmmirror）
set -e
cd "$(dirname "$0")/.."

NODE_VERSION="${NODE_VERSION:-20.19.0}"
FNPACK_VERSION="${FNPACK_VERSION:-1.2.3}"
FNPACK_URL="${FNPACK_URL:-https://static2.fnnas.com/fnpack/fnpack-${FNPACK_VERSION}-linux-amd64}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"

# Node 自举
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
echo "==> Node: $(node -v)  npm: $(npm -v)"

# 前端构建
echo "==> frontend build"
cd frontend
npm config set registry "$NPM_REGISTRY"
npm ci --no-fund --no-audit || {
  echo "❌ npm ci 失败（锁文件不一致或依赖解析失败）"
  echo "   请本地执行 npm install 并提交更新后的 package-lock.json"
  exit 1
}
npm run build
cd ..

# fnpack
FNPACK_BIN="${HOME}/.cache/fnpack/fnpack"
mkdir -p "$(dirname "$FNPACK_BIN")"
if [ ! -x "$FNPACK_BIN" ]; then
  echo "==> download fnpack v${FNPACK_VERSION}"
  curl -fsSL "$FNPACK_URL" -o "$FNPACK_BIN"
  chmod +x "$FNPACK_BIN"
fi

# 打包
echo "==> fnpack build"
PYTHON="$(command -v python3)" FNPACK="$FNPACK_BIN" bash scripts/build_fpk.sh

# 产物重命名：同时输出固定名和带版本号副本
echo "==> 构建产物：$(pwd)/fn-finstat.fpk"
sha256sum fn-finstat.fpk
APP_VERSION="$(cat VERSION | tr -d '\r' | tr -d ' \n')"
[ -z "$APP_VERSION" ] && APP_VERSION=unknown
echo "==> 应用版本：${APP_VERSION}"
FPK_VERSIONED="fn-finstat-v${APP_VERSION}.fpk"
cp fn-finstat.fpk "${FPK_VERSIONED}"
echo "==> 产物带版本号副本：${FPK_VERSIONED}"

echo "==> 构建打包完成 ✅"
