"""集中式权限控制中间件：身份认证与管理面（管理员专属接口）在路由分发前统一收口

权限模型（与飞牛网关身份体系一致，解析见 core/context.py）：
- 网关模式（IS_FNOS）：X-Trim-Userid 注入即登录账号；管理员由 X-Trim-Isadmin=true 声明；
  **缺失身份头一律 401 拒绝**（身份未知 ≠ 权限不足），排障逃生开关见 config.ALLOW_HEADERLESS
- 本地/独立部署：无身份头视为单机唯一用户，全量放行（与 deps.require_admin 同语义）

策略表 ADMIN_RULES 按「HTTP 方法 + 路径正则」枚举全部管理面（全局影响操作：
备份/恢复、数据库迁移、运行日志、NAS/AI/分类/自动化的写操作）。规则不命中
即放行 —— 普通接口的数据隔离由服务层按 user_id 过滤保证，不依赖本中间件。

双层防御：
1. 本中间件在路由分发前拦截，新端点漏挂路由守卫时兜底（无头默认 401、管理面默认拒绝）；
2. deps.require_admin 路由守卫提供 OpenAPI 文档语义的 401/403，两层文案一致。
新增管理员接口时必须同时更新 ADMIN_RULES 与路由守卫（并补一条测试）。
"""

import logging
import re

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import ALLOW_HEADERLESS, API_BASE_PATH, IS_FNOS
from app.core.constants import API_TOKEN_PREFIX
from app.core.context import GatewayUser, gateway_user_from_headers
from app.core.errors import BizError, ErrorCode

logger = logging.getLogger(__name__)

# 管理面拦截文案：与 deps.require_admin 抛出的 PermissionDeniedError 保持一致
ADMIN_ONLY_MSG = "该操作仅限管理员账号"
# API Token 请求的只读限制文案（Token 恒为只读凭证，写方法一律 403）
TOKEN_READONLY_MSG = "API Token 仅支持只读访问"
# Token 无效文案：与 deps.get_identity 抛出的 UnauthorizedError 保持一致
TOKEN_INVALID_MSG = "无效的 API Token"
# 未认证拦截文案：与 deps.require_admin 抛出的 UnauthorizedError 保持一致
UNAUTHENTICATED_MSG = "请通过飞牛桌面访问本应用"

# 中间件验证通过的 Token 身份在 ASGI scope 中的暂存键（deps.get_identity 复用，
# 同一请求不做第二次 Token 查库）
SCOPE_TOKEN_USER_KEY = "fn_finstat_token_user"

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
    # ---- 通知：配置保存与出站测试（列表/角标/已读保持普通账号可用）----
    ({"PUT"}, re.compile(r"^/api/settings/notify/config$")),
    ({"POST"}, re.compile(r"^/api/settings/notify/webhook-test$")),
    # ---- 分类学习规则：全局共享、影响所有账号的导入归类（列表保持普通账号可读）----
    ({"PUT", "DELETE"}, re.compile(r"^/api/settings/learned-rules/\d+$")),
    # ---- 账本：全局共享维度，新建/改名/删除会改变所有账号的数据归属 ----
    ({"POST"}, re.compile(r"^/api/ledgers$")),
    ({"PUT", "DELETE"}, re.compile(r"^/api/ledgers/\d+$")),
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


def client_key(scope: Scope) -> str:
    """从 ASGI scope 提取客户端标识（用于认证失败限流计数）

    取 `client[0]`（直连来源 IP）。**不信任 `X-Forwarded-For`**：本应用要么跑在
    fnOS 网关的 Unix Socket 后、要么独立部署直连，都不经过可信反向代理；
    信任该头会让攻击者用一个随机头绕过限流（反而比不取更糟）。取不到时
    返回空串，由限流器退化为全局键（仍能防住无限枚举）。
    """
    client = scope.get("client")
    if not client:
        return ""
    try:
        return str(client[0])
    except (IndexError, TypeError):
        return ""


def has_api_token(raw: dict[str, str]) -> bool:
    """请求头里是否携带本应用的开放 API Token（仅认 ffk_ 前缀凭证）"""
    return token_from_headers(raw) is not None


def token_from_headers(raw: dict[str, str]) -> str | None:
    """从请求头取 Token 明文；无/非本应用凭证返回 None（deps 与中间件共用）

    只认 ``ffk_`` 前缀（core.constants.API_TOKEN_PREFIX）：Authorization 头可能
    被前置代理、内网工具注入自己的 Basic/Bearer 凭证，若照单全收，独立部署下
    这些请求会被误判为「携带无效 Token」而 401。
    """
    auth = (raw.get("authorization") or "").strip()
    if auth[:7].lower() == "bearer ":
        candidate = auth[7:].strip()
        return candidate if candidate.startswith(API_TOKEN_PREFIX) else None
    token = (raw.get("x-api-token") or "").strip()
    return token if token.startswith(API_TOKEN_PREFIX) else None


def is_admin_surface(path: str, method: str) -> bool:
    """判定「方法 + 路径」是否属于管理面（与用户身份无关的纯策略查询）"""
    p = strip_api_prefix(path)
    for methods, pattern in _ADMIN_RULES:
        if method in methods and pattern.match(p):
            return True
    return False


def access_rejection(user: GatewayUser, path: str, method: str) -> int | None:
    """访问判定：返回拒绝的 HTTP 状态码，None 表示放行

    - 网关模式下缺失身份头（user_id 为空）= 未认证，对全部路径回 401：
      身份未知时既不能放行管理面，也不能放行普通接口 —— 数据隔离依赖
      user_id 过滤，空身份请求写入的数据会落在一个不存在的账号上；
    - 独立部署/本地开发保留「无头 = 单机唯一用户」放行（可通过
      FNOS_ALLOW_HEADERLESS=1 逃生开关在网关模式临时恢复此行为）；
    - 管理面要求管理员，与是否登录无关。
    """
    if not user.user_id:
        if IS_FNOS and not ALLOW_HEADERLESS:
            return 401
        return None
    if is_admin_surface(path, method) and not user.is_admin:
        return 403
    return None


class PermissionMiddleware:
    """路由分发前的身份认证 + 管理面门禁：直接回 401/403 统一响应体

    响应结构与 core/handlers.py 的统一包装一致（401→code=10006、403→code=10002），
    前端无需区分拦截来自中间件还是路由守卫；被拦截请求不进入路由与业务层。
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
        method = scope.get("method", "GET")

        # ---- API Token 分支（T-1.2）：无网关头但携带 Token 的请求 ----
        # Token 恒为只读且非管理员：写方法与管理面在此直接拒绝（不到路由层）。
        # Token **在中间件内即验证**（安全收口）：此前只检查「带了 Token 串」
        # 就放行只读请求、验证推给路由层 deps——但无身份依赖的路由（如
        # GET /api/category、/docs）不做验证，任意垃圾字符串即可绕过 401。
        # 现在验证失败（含认证库暂不可用）一律 401 fail-closed；验证通过的身份
        # 暂存 scope，deps.get_identity 直接复用，同一请求不二次查库。
        #
        # ⚠️ 仅在请求**确实没有网关身份**时才走本分支。否则「任意字符串 Token
        # + 一个有 user_id 的头」就能让本中间件跳过全部判定（实测：无身份头
        # 的请求夹带伪造 Token 时，本该 401 却走到路由层）。带网关头时 Token
        # 不参与鉴权（与 deps.get_identity 的「网关头优先」同语义）。
        if not user.user_id and has_api_token(raw):
            # 惰性导入：core 层不反向依赖 services（防循环导入）；
            # 认证是一次索引查询，仅 Token 请求走到这里，阻塞可忽略
            from app.services import token_service

            # 认证失败限流（M13-4）：失败过多在 service 层抛 429，此处转统一响应体。
            # 中间件不经过全局异常处理器，故必须显式捕获并翻译。
            try:
                resolved = token_service.authenticate(
                    token_from_headers(raw) or "", client_key(scope)
                )
            except BizError as exc:
                response = JSONResponse(
                    status_code=exc.http_status,
                    content={"code": exc.code, "msg": exc.message, "data": None},
                )
                await response(scope, receive, send)
                return
            if resolved is None:
                response = JSONResponse(
                    status_code=401,
                    content={
                        "code": ErrorCode.UNAUTHORIZED,
                        "msg": TOKEN_INVALID_MSG,
                        "data": None,
                    },
                )
                await response(scope, receive, send)
                return
            if is_admin_surface(path, method):
                response = JSONResponse(
                    status_code=403,
                    content={
                        "code": ErrorCode.FORBIDDEN,
                        "msg": ADMIN_ONLY_MSG,
                        "data": None,
                    },
                )
                await response(scope, receive, send)
                return
            if method not in ("GET", "HEAD", "OPTIONS"):
                response = JSONResponse(
                    status_code=403,
                    content={
                        "code": ErrorCode.FORBIDDEN,
                        "msg": TOKEN_READONLY_MSG,
                        "data": None,
                    },
                )
                await response(scope, receive, send)
                return
            scope[SCOPE_TOKEN_USER_KEY] = resolved
            await self.app(scope, receive, send)
            return

        rejection = access_rejection(user, path, method)
        if rejection is None:
            await self.app(scope, receive, send)
            return
        logger.info(
            "访问拦截：HTTP %s %s -> %d（账号 %s）",
            scope.get("method", "-"),
            path,
            rejection,
            user.user_id or "-",
        )
        if rejection == 401:
            body = {
                "code": ErrorCode.UNAUTHORIZED,
                "msg": UNAUTHENTICATED_MSG,
                "data": None,
            }
        else:
            body = {
                "code": ErrorCode.FORBIDDEN,
                "msg": ADMIN_ONLY_MSG,
                "data": None,
            }
        response = JSONResponse(status_code=rejection, content=body)
        await response(scope, receive, send)
