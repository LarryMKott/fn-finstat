#!/bin/bash
# CI 环境准备：apt 换清华源 + python3/pip 安装 + pip 加速配置
# 只负责安装 python3/pip 基础运行时，不装任何 Python 包
# 测试依赖由 ci_test.sh 安装，构建脚本依赖仅需标准库
set -e
cd "$(dirname "$0")/.."

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
