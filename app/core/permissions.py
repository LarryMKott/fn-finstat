"""集中式权限控制中间件：管理面（管理员专属接口）在路由分发前统一收口

权限模型（与飞牛网关身份体系一致，解析见 core/context.py）：
- 网关模式：X-Trim-Userid 注入即登录账号；管理员由 X-Trim-Isadmin=true 声明
- 本地/独立部署：无身份头视为单机唯一用户，全量放行（与 deps.require_admin 同语义）

策略表 ADMIN_RULES 按「HTTP 方法 + 路径正则」枚举全部管理面（全局影响操作：
备份/恢复、数据库迁移、运行日志、NAS/AI/分类/自动化的写操作）。规则不命中
即放行 —— 普通接口的数据隔离由服务层按 user_id 过滤保证，不依赖本中间件。

双层防御：
1. 本中间件在路由分发前拦截，新端点漏挂路由守卫时兜底（默认拒绝管理面）；
2. deps.require_admin 路由守卫提供 OpenAPI 文档语义的 403，两层文案一致。
新增管理员接口时必须同时更新 ADMIN_RULES 与路由守卫（并补一条测试）。
"""

import logging
import re

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import API_BASE_PATH
from app.core.context import GatewayUser, gateway_user_from_headers
from app.core.errors import ErrorCode

logger = logging.getLogger(__name__)

# 管理面拦截文案：与 deps.require_admin 抛出的 PermissionDeniedError 保持一致
ADMIN_ONLY_MSG = "该操作仅限管理员账号"

# 管理面策略表：(方法集合, 路径正则)。路径为剥掉向导接口前缀后的根路径。
_ADMIN_RULES: list[tuple[set[str], re.Pattern[str]]] = [
    # ---- 应用设置：认领历史数据 / 数据库迁移 / 运行日志 / 备份恢复 ----
    ({"POST"}, re.compile(r"^/api/settings/user/claim$")),
    ({"POST"}, re.compile(r"^/api/settings/database/(?:test|migrate)$")),
    ({"GET"}, re.compile(r"^/api/settings/logs(?:/download)?$")),
    ({"GET"}, re.compile(r"^/api/settings/backup$")),
    ({"POST"}, re.compile(r"^/api/settings/restore$")),
    # ---- 自动化：手动执行 / 启停 / 调整间隔（列表与运行历史保持普通账号可读）----
    ({"POST"}, re.compile(r"^/api/settings/automation/[^/]+/(?:run|toggle)$")),
    ({"PUT"}, re.compile(r"^/api/settings/automation/[^/]+$")),
    # ---- NAS / AI / 分类：应用级共享配置与全局数据的写操作 ----
    ({"PUT"}, re.compile(r"^/api/nas/config$")),
    ({"PUT"}, re.compile(r"^/api/ai/config$")),
    ({"POST"}, re.compile(r"^/api/ai/test$")),
    ({"POST"}, re.compile(r"^/api/category$")),
    ({"PUT", "DELETE"}, re.compile(r"^/api/category/\d+$")),
]


def strip_api_prefix(path: str) -> str:
    """剥掉向导配置的接口地址前缀（路由同时挂载在根路径与前缀下）"""
    if API_BASE_PATH and API_BASE_PATH != "/" and path.startswith(API_BASE_PATH + "/"):
        return path[len(API_BASE_PATH) :]
    return path


def is_admin_surface(path: str, method: str) -> bool:
    """判定「方法 + 路径」是否属于管理面（与用户身份无关的纯策略查询）"""
    p = strip_api_prefix(path)
    for methods, pattern in _ADMIN_RULES:
        if method in methods and pattern.match(p):
            return True
    return False


def permits(user: GatewayUser, path: str, method: str) -> bool:
    """放行判定：非管理面一律放行；管理面要求管理员，本地单机模式除外"""
    if not is_admin_surface(path, method):
        return True
    # 无网关身份头 = 本地开发/独立部署的单机唯一用户，全量放行
    return not user.user_id or user.is_admin


class PermissionMiddleware:
    """路由分发前的管理面门禁：非管理员访问管理面时直接回 403（统一响应体）

    响应结构与 core/handlers.py 的统一包装一致（code=10002），前端无需区分
    拦截来自中间件还是路由守卫；被拦截请求不进入路由与业务层。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw: dict[str, str] = {}
        for key, value in scope.get("headers") or []:
            raw.setdefault(key.decode("latin-1").lower(), value.decode("latin-1"))
        user = gateway_user_from_headers(raw)
        path = scope.get("path", "")
        if permits(user, path, scope.get("method", "GET")):
            await self.app(scope, receive, send)
            return
        logger.info(
            "权限拦截：HTTP %s %s（账号 %s）",
            scope.get("method", "-"),
            path,
            user.user_id or "-",
        )
        response = JSONResponse(
            status_code=403,
            content={"code": ErrorCode.FORBIDDEN, "msg": ADMIN_ONLY_MSG, "data": None},
        )
        await response(scope, receive, send)
