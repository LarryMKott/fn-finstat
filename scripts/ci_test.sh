#!/bin/bash
# CI 测试门禁：安装测试依赖 + 单元测试 + ruff 静态检查
# 前置：ci_env.sh 已安装 python3/pip
set -e
cd "$(dirname "$0")/.."

PYTHON="$(command -v python3)"

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
