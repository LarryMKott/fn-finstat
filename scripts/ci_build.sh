#!/bin/bash
# CI 自包含构建脚本：环境准备 → 测试门禁 → 构建打包
# 适用于 Gitee Go 或其他 Linux CI 环境
#
# 流程：
#   1. 环境准备 — apt 换清华源 + python3/pip 安装 + pip 加速配置
#   2. 测试门禁 — 安装测试依赖 + 单元测试 + ruff 静态检查
#   3. 构建打包 — Node 自举 + 前端构建 + fnpack 打包 + 产物重命名
#                （构建脚本只用 Python 标准库，无需 pip 装包）
#
# 可用环境变量：
#   SKIP_TESTS=1     跳过测试门禁（仅限紧急调试）
#   NODE_VERSION     Node 版本（默认 24.18.0）
#   FNPACK_VERSION    fnpack 版本（默认 1.2.3）
#   NPM_REGISTRY      npm 镜像源（默认 npmmirror）
set -e
cd "$(dirname "$0")/.."

# ============================================================
# 1. 环境准备
# ============================================================

# pip 国内加速：清华 TUNA 镜像 + 固定缓存目录
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-pypi.tuna.tsinghua.edu.cn}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$HOME/.cache/pip}"

# apt 换源 + 安装 python3/pip
setup_apt_mirror() {
  if [ ! -f /etc/os-release ]; then return; fi
  . /etc/os-release
  case "$ID" in
    ubuntu|debian)
      if grep -q "mirrors.tuna.tsinghua.edu.cn" /etc/apt/sources.list 2>/dev/null; then
        return
      fi
      local codename="${VERSION_CODENAME:-}"
      [ -z "$codename" ] && codename="$(lsb_release -cs 2>/dev/null || echo stable)"
      echo "==> 切换 apt 源到清华镜像（$ID $codename）"
      [ ! -f /etc/apt/sources.list.bak ] && cp /etc/apt/sources.list /etc/apt/sources.list.bak 2>/dev/null || true
      if [ "$ID" = "ubuntu" ]; then
        cat > /etc/apt/sources.list <<EOF
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ $codename main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ $codename-updates main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ $codename-security main restricted universe multiverse
EOF
      else
        cat > /etc/apt/sources.list <<EOF
deb https://mirrors.tuna.tsinghua.edu.cn/debian/ $codename main
deb https://mirrors.tuna.tsinghua.edu.cn/debian/ $codename-updates main
deb https://mirrors.tuna.tsinghua.edu.cn/debian-security/ $codename-security main
EOF
      fi
      ;;
  esac
}

echo "==> 环境检查与准备"

if ! command -v python3 >/dev/null 2>&1; then
  echo "==> python3 缺失，尝试安装"
  setup_apt_mirror
  apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv \
    || yum install -y -q python3 python3-pip \
    || { echo "错误：无法安装 python3"; exit 1; }
fi
if ! python3 -m pip --version >/dev/null 2>&1; then
  echo "==> pip 缺失，尝试安装"
  setup_apt_mirror
  apt-get update -qq && apt-get install -y -qq python3-pip \
    || yum install -y -q python3-pip \
    || { echo "错误：无法安装 pip"; exit 1; }
fi

echo "==> python3: $(python3 --version)"
echo "==> pip: $(python3 -m pip --version)"
echo "==> PIP_INDEX_URL: ${PIP_INDEX_URL}"

PYTHON="$(command -v python3)"

# ============================================================
# 2. 测试门禁（SKIP_TESTS=1 可跳过）
# ============================================================
if [ "${SKIP_TESTS:-0}" != "1" ]; then
  # 安装测试依赖
  echo "==> 安装测试依赖"
  "$PYTHON" -m pip install --disable-pip-version-check \
    -r app/requirements.txt pytest httpx ruff \
    || "$PYTHON" -m pip install --disable-pip-version-check --break-system-packages \
      -r app/requirements.txt pytest httpx ruff

  # 单元测试
  echo "==> 单元测试门禁"
  bash scripts/run_tests.sh

  # ruff 静态检查（只拦 F + E9 真问题）
  echo "==> 静态检查门禁：ruff check --select F,E9"
  ruff check app cmd scripts tests --select F,E9 || {
    echo "❌ 静态检查未通过"
    exit 1
  }
  echo "==> 测试门禁全部通过 ✅"
fi

# ============================================================
# 3. 构建打包
# ============================================================
NODE_VERSION="${NODE_VERSION:-24.18.0}"
FNPACK_VERSION="${FNPACK_VERSION:-1.2.3}"
FNPACK_URL="${FNPACK_URL:-https://static2.fnnas.com/fnpack/fnpack-${FNPACK_VERSION}-linux-amd64}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"

# Node 自举（Gitee Go build@nodejs 应已自带，但兜底以防 PATH 问题）
need_node=1
if command -v node >/dev/null 2>&1; then
  major="$(node -v | sed 's/^v\([0-9]*\).*/\1/')"
  [ "${major:-0}" -ge 18 ] && need_node=0
fi
if [ "$need_node" = 1 ]; then
  echo "==> bootstrap Node v${NODE_VERSION}"
  mkdir -p /tmp/node24
  curl -fsSL "https://npmmirror.com/mirrors/node/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.gz" \
    | tar -xz -C /tmp/node24 --strip-components=1
  export PATH="/tmp/node24/bin:$PATH"
fi
# 兜底：确保 npm 在 PATH 中
if ! command -v npm >/dev/null 2>&1; then
  export PATH="/tmp/node24/bin:$PATH"
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

# 打包（构建脚本只用 Python 标准库，pip 无需装包；SKIP_TESTS=1 避免重复跑测试）
echo "==> fnpack build"
PYTHON="$PYTHON" FNPACK="$FNPACK_BIN" SKIP_TESTS=1 bash scripts/build_fpk.sh

# 产物重命名：同时输出固定名和带版本号副本
echo "==> 构建产物：$(pwd)/fn-finstat.fpk"
FPK_SHA256="$(sha256sum fn-finstat.fpk | awk '{print $1}')"
echo "==> SHA-256：${FPK_SHA256}"
APP_VERSION="$(cat VERSION | tr -d '\r' | tr -d ' \n')"
[ -z "$APP_VERSION" ] && APP_VERSION=unknown
echo "==> 应用版本：${APP_VERSION}"
FPK_VERSIONED="fn-finstat-v${APP_VERSION}.fpk"
cp fn-finstat.fpk "${FPK_VERSIONED}"
echo "==> 产物带版本号副本：${FPK_VERSIONED}"

# ============================================================
# 4. 生成 Release 说明（releaseNode.txt）
#    release@gitee 的 description 支持 "兜底文本 | 文件路径" 语法，
#    会读取该文件内容作为 Release 描述，因此这里先把它生成出来。
#    日志生成失败不能阻断发布，故有任何异常都回退为原始提交列表。
# ============================================================
echo "==> 生成 Release 说明"
# Gitee Go 可能是浅克隆，缺少历史会导致无法推断"上次发版到哪"，先尝试补全
git fetch --unshallow --tags >/dev/null 2>&1 || git fetch --tags >/dev/null 2>&1 || true

if ! "$PYTHON" scripts/gen_release_notes.py \
      --output releaseNode.txt \
      --sha256 "${FPK_SHA256}" 2>&1; then
  echo "⚠️ 结构化日志生成失败，回退为原始提交列表"
  {
    echo "## fn-finstat v${APP_VERSION}"
    echo ""
    echo "> ⚠️ 自动整理日志失败，以下为最近提交的原始列表"
    echo ""
    git log --no-merges -20 --pretty="- %s (%h)" 2>/dev/null || true
  } > releaseNode.txt
fi

# 兜底：确保文件非空，否则 release 插件会回落到 yml 里的兜底描述
if [ ! -s releaseNode.txt ]; then
  echo "## fn-finstat v${APP_VERSION}（更新日志生成异常，详见构建日志）" > releaseNode.txt
fi
echo "==> Release 说明预览："
head -20 releaseNode.txt

echo "==> 构建打包完成 ✅"
