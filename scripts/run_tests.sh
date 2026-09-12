#!/bin/bash
# 一键运行所有单元测试（本地开发与打包/CI 门禁共用入口）
# 用法：
#   bash scripts/run_tests.sh                          # 自动选择 Python
#   PYTHON=/path/to/python bash scripts/run_tests.sh   # 指定 Python
#
# Python 选择顺序：$PYTHON > app/venv（Windows/Linux 布局）> python3 > python。
# 测试依赖缺失时自动 pip 安装 app/requirements.txt + pytest + httpx；
# 系统 Python 受 PEP 668 保护（Ubuntu 23+/Debian 12）时自动加 --break-system-packages 重试。
# 退出码：0 = 全部通过；非 0 = 存在失败或环境不可用（打包脚本据此中断构建）。
set -e
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for cand in app/venv/Scripts/python.exe app/venv/bin/python python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      PYTHON="$cand"
      break
    fi
  done
fi
if [ -z "$PYTHON" ]; then
  echo "❌ 未找到可用的 Python，请安装 python3 或用 PYTHON=... 指定路径" >&2
  exit 1
fi
echo "==> Python: $("$PYTHON" --version 2>&1)（"$("$PYTHON" -c 'import sys; print(sys.executable)')"）"

# 测试依赖缺失时自动安装（本地 venv 已就绪时零安装直接跑）
if ! "$PYTHON" -c "import pytest, httpx, fastapi, sqlalchemy, openpyxl" >/dev/null 2>&1; then
  echo "==> 安装测试依赖（app/requirements.txt + pytest + httpx）"
  "$PYTHON" -m pip install --disable-pip-version-check -r app/requirements.txt pytest httpx \
    || "$PYTHON" -m pip install --disable-pip-version-check --break-system-packages \
      -r app/requirements.txt pytest httpx
fi

echo "==> 运行单元测试"
if "$PYTHON" -m pytest; then
  echo "✅ 全部测试通过"
else
  echo "❌ 存在失败的测试" >&2
  exit 1
fi
