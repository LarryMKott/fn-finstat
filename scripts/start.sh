#!/bin/bash
# 本地开发启动脚本（模拟 fnOS 环境变量，本地数据写入项目根 .local_data）
# 生产环境请使用 cmd/main 生命周期脚本
#
# 端口优先级：wizard_port（向导/环境注入）> PORT > 默认 8090，
# 端口被其它程序占用时可通过任一变量改用空闲端口。
cd "$(dirname "$0")/.." || exit 1
PORT="${wizard_port:-${PORT:-8090}}"
case "$PORT" in
  ''|*[!0-9]*) PORT=8090 ;;
esac
# 监听地址默认只绑回环：应用把 X-Trim-Userid / X-Trim-Isadmin 请求头当作网关注入的
# 可信身份（见 app/api/deps.py），绑 0.0.0.0 时同网段任何人都能伪造这两个头直接成为
# 管理员。需要用手机等局域网设备调试时显式指定：HOST=0.0.0.0 ./scripts/start.sh
HOST="${HOST:-127.0.0.1}"
echo "财务统计服务启动中: http://127.0.0.1:${PORT}/app/fn-finstat/  接口文档: http://127.0.0.1:${PORT}/docs"
if [ "$HOST" != "127.0.0.1" ]; then
  echo "⚠️  监听 ${HOST}：同网段设备均可访问，且 X-Trim-* 身份头可被伪造 —— 仅在可信网络使用"
fi
exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT"
