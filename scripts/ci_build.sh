#!/bin/bash
# CI 自包含构建脚本：环境准备 -> 测试门禁 -> 构建打包
# 适用于 Gitee Go 单 step 或其他 Linux CI 环境；
# Gitee Go 推荐用拆分为多个 step 的 build-fpk.yml
# 可用环境变量：SKIP_TESTS=1 跳过测试门禁（仅限紧急调试）
set -e
cd "$(dirname "$0")/.."

bash scripts/ci_env.sh

if [ "${SKIP_TESTS:-0}" != "1" ]; then
  bash scripts/ci_test.sh
fi

bash scripts/ci_package.sh
