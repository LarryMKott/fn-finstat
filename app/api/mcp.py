"""MCP Server 端点（T-1.1）：Model Context Protocol Streamable HTTP（JSON-RPC 2.0）

- 零新依赖：手写 initialize / tools/list / tools/call / ping 四个方法，
  工具集见 services/mcp_service（全部只读）；
- 鉴权与全应用同链：网关头优先、API Token 兜底（fnOS 模式下无 Token 请求
  会被权限中间件 401）；本地/独立部署无头 = 单机用户；
- config.MCP_ENABLED=0 时端点整体 404（外部集成不需要时收窄攻击面）。
"""

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import CurrentUser, request_db_session
from app.config import APP_VERSION, MCP_ENABLED
from app.services import mcp_service

router = APIRouter(
    prefix="/api/mcp",
    tags=["MCP Server"],
    dependencies=[Depends(request_db_session)],
)

_PROTOCOL_VERSION = "2025-03-26"


def _rpc_result(rpc_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _rpc_error(rpc_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


@router.post(
    "",
    summary="MCP Streamable HTTP（JSON-RPC 2.0：initialize / tools/list / tools/call）",
)
async def mcp_endpoint(request: Request, user: CurrentUser):
    if not MCP_ENABLED:
        return Response(status_code=404)
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return Response(
            content='{"jsonrpc":"2.0","id":null,"error":{"code":-32700,"message":"Parse error"}}',
            media_type="application/json",
            status_code=400,
        )
    rpc_id = body.get("id")
    method = body.get("method", "")

    # 通知（无 id）：initialized 等无需响应内容，按协议回 202 空体
    if "id" not in body:
        return Response(status_code=202)

    if method == "initialize":
        return _rpc_result(
            rpc_id,
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {**mcp_service.SERVER_INFO, "version": APP_VERSION},
            },
        )
    if method == "ping":
        return _rpc_result(rpc_id, {})
    if method == "tools/list":
        return _rpc_result(rpc_id, {"tools": mcp_service.list_tools()})
    if method == "tools/call":
        params = body.get("params") or {}
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            text = mcp_service.call_tool(name, arguments, user.user_id)
        except ValueError as exc:
            return _rpc_result(
                rpc_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
        return _rpc_result(
            rpc_id,
            {"content": [{"type": "text", "text": text}], "isError": False},
        )
    return _rpc_error(rpc_id, -32601, f"Method not found: {method}")


@router.get(
    "", summary="MCP 端点不支持 GET（无服务端主动推送）", include_in_schema=False
)
async def mcp_get():
    # Streamable HTTP：无服务端流时 GET /mcp 返回 405（协议允许）
    return Response(status_code=405)
