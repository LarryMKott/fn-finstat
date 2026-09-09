#!/bin/bash
# 本地开发启动脚本（模拟 fnOS 环境变量，本地数据写入项目根 .local_data）
# 生产环境请使用 cmd/main 生命周期脚本
cd "$(dirname "$0")/.." || exit 1
PORT="${PORT:-8090}"
echo "财务统计服务启动中: http://127.0.0.1:${PORT}/app/fn-finstat/  接口文档: http://127.0.0.1:${PORT}/docs"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
