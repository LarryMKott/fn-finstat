#!/bin/bash
# CI 自包含构建脚本：单元测试门禁 -> 前端构建 -> 下载 fnpack -> 打包 FPK（含权限修复与设备布局自检）
# 适用于 Gitee Go（build@nodejs 云端编译）或其他 Linux CI 环境。
# 国内加速：apt 换清华源、pip 用清华 TUNA + 固定缓存目录、Node 用 npmmirror、fnpack 用 fnnas CDN。
# 可用环境变量：
#   NODE_VERSION   自举的 Node 版本（默认 20.19.0，基础镜像 Node >= 18 时跳过自举）
#   FNPACK_VERSION fnpack 版本（默认 1.2.3）
#   NPM_REGISTRY   npm 镜像源（默认 npmmirror，国内加速）
#   PIP_INDEX_URL  pip 镜像源（默认清华 TUNA，国内加速）
#   PIP_CACHE_DIR  pip 下载缓存目录（默认 ~/.cache/pip，跨构建复用）
#   SKIP_TESTS     设为 1 跳过单元测试门禁（仅限紧急调试，勿用于发布）
set -e
cd "$(dirname "$0")/.."

NODE_VERSION="${NODE_VERSION:-20.19.0}"
FNPACK_VERSION="${FNPACK_VERSION:-1.2.3}"
FNPACK_URL="${FNPACK_URL:-https://static2.fnnas.com/fnpack/fnpack-${FNPACK_VERSION}-linux-amd64}"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"

# pip 国内加速：清华 TUNA 镜像 + 固定缓存目录（便于 Gitee Go 跨构建复用，
# 避免每次重下几十 MB 的 fastapi/sqlalchemy/pytest 等）
export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-pypi.tuna.tsinghua.edu.cn}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$HOME/.cache/pip}"

# 1. python3 + pip：单元测试门禁与打包自检（fix_fpk_perm.py）都依赖
#    Gitee Go 基础镜像（Ubuntu/Debian）apt 默认源在境外，国内构建慢，
#    换清华源加速 python3/pip 安装
setup_apt_mirror() {
  if [ ! -f /etc/os-release ]; then return; fi
  . /etc/os-release
  case "$ID" in
    ubuntu|debian)
      # 已是清华源则跳过，避免重复改写
      if grep -q "mirrors.tuna.tsinghua.edu.cn" /etc/apt/sources.list 2>/dev/null; then
        return
      fi
      local codename="${VERSION_CODENAME:-}"
      [ -z "$codename" ] && codename="$(lsb_release -cs 2>/dev/null || echo stable)"
      echo "==> 切换 apt 源到清华镜像（$ID $codename）"
      # 备份原文件（首次切换时），便于回滚
      [ ! -f /etc/apt/sources.list.bak ] && cp /etc/apt/sources.list /etc/apt/sources.list.bak 2>/dev/null || true
      # Debian 用 main，Ubuntu 用 main restricted universe multiverse
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

if ! command -v python3 >/dev/null 2>&1; then
  echo "==> python3 缺失，尝试安装"
  setup_apt_mirror
  apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv \
    || yum install -y -q python3 python3-pip \
    || { echo "错误：无法安装 python3，无法执行测试门禁与打包自检"; exit 1; }
fi
# pip 缺失时补装（某些精简镜像只带 python3 不带 pip）
if ! python3 -m pip --version >/dev/null 2>&1; then
  echo "==> pip 缺失，尝试安装"
  setup_apt_mirror
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

# 2.5 静态检查门禁：只拦 F（未定义 / 未使用）与 E9（语法、IO 错误）两类真问题。
#     全量规则（380 余条，绝大多数是类型注解与 import 排序等风格项）暂不纳入，
#     避免一上来就阻塞构建；待存量清理后再逐步收紧。见 2026-09-14 审查报告 §4。
if ! command -v ruff >/dev/null 2>&1; then
  echo "==> 安装 ruff（静态检查门禁）"
  python3 -m pip install --quiet ruff \
    || echo "⚠️  ruff 安装失败，跳过静态检查门禁"
fi
if command -v ruff >/dev/null 2>&1; then
  echo "==> 静态检查门禁：ruff check --select F,E9"
  ruff check app cmd scripts tests --select F,E9 || {
    echo "❌ 静态检查未通过 —— 请修复上述 F/E9 问题后重新构建"
    exit 1
  }
fi

# 3. Node 自举：基础镜像自带 Node < 18（无法运行 Vite 5）时，下载便携版 Node 20
# 优先使用 PATH 中的全局安装（本地为 24.21.0 即满足 >= 18，跳过自举）；
# 仅在环境缺少 Node >= 18（旧 CI 镜像）时才下载 NODE_VERSION 指定的便携版
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
echo "==> Node 来源：$(command -v node)"
node -v
npm -v

# 4. 前端构建（产物输出至 app/static）
echo "==> frontend build"
cd frontend
npm config set registry "$NPM_REGISTRY"
# 锁文件不一致时直接失败：回退 npm install 会绕过 lockfile 重建依赖树，导致
# 构建出的前端与仓库提交的依赖版本长期漂移，且失败被 || 吞掉、坏产物一路带到 fpk。
# 依赖需要升级时，请本地 npm install 后提交更新后的 package-lock.json。
npm ci --no-fund --no-audit || {
  echo "❌ npm ci 失败（锁文件不一致或依赖解析失败）—— 勿回退 npm install；"
  echo "   请本地执行 npm install 并提交更新后的 package-lock.json"
  exit 1
}
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
# 打印应用版本（来源 VERSION 文件，构建时由 sync_version.py 同步到 manifest
# 与 config.py），便于将产物与 fnOS 应用「设置 > 关于」中显示的版本对应。
APP_VERSION="$(cat VERSION | tr -d '\r' | tr -d ' \n')"
[ -z "$APP_VERSION" ] && APP_VERSION=unknown
echo "==> 应用版本：${APP_VERSION}"
