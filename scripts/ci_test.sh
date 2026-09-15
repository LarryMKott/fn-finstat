#!/bin/bash
# CI 测试门禁：单元测试 + ruff 静态检查
# 要求先运行 ci_env.sh 确保 python3/pip 已就绪
set -e
cd "$(dirname "$0")/.."

# 单元测试
echo "==> 单元测试门禁"
PYTHON="$(command -v python3)" bash scripts/run_tests.sh

# ruff 静态检查（只拦 F + E9 真问题）
if ! command -v ruff >/dev/null 2>&1; then
  echo "==> 安装 ruff"
  python3 -m pip install --quiet ruff \
    || echo "⚠️  ruff 安装失败，跳过静态检查"
fi
if command -v ruff >/dev/null 2>&1; then
  echo "==> 静态检查门禁：ruff check --select F,E9"
  ruff check app cmd scripts tests --select F,E9 || {
    echo "❌ 静态检查未通过"
    exit 1
  }
fi

echo "==> 测试门禁全部通过 ✅"
