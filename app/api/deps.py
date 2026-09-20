"""请求级公共依赖：网关身份解析、管理员守卫与请求级数据库会话

身份对象（GatewayUser）与主题归一化定义在 app/core/context.py（服务层可引用），
本模块只提供 FastAPI 依赖注入函数（路由层专用）。

⚠️ 身份头的可信前提：X-Trim-* 头由飞牛网关注入，本模块**无条件信任**，不做来源
校验。因此应用绝不能直接暴露在不可信网络：
- fnOS 网关模式：cmd/main 以 `uvicorn --uds` 启动，只绑 Unix Socket 不监听 TCP；
- 独立部署/本地运行：config.HOST 默认绑 127.0.0.1（详见 config.py 的 HOST 注释）。
任一前提下被绕过，攻击者伪造两个头即可提权为管理员。
"""

from collections.abc import Iterator
from typing import Annotated, Optional

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.config import ALLOW_HEADERLESS, IS_FNOS
from app.core.context import GatewayUser, gateway_user_from_headers, normalize_theme
from app.core.errors import PermissionDeniedError, UnauthorizedError
from app.core.permissions import (
    ADMIN_ONLY_MSG,
    SCOPE_TOKEN_USER_KEY,
    TOKEN_INVALID_MSG,
    TOKEN_READONLY_MSG,
    UNAUTHENTICATED_MSG,
    token_from_headers,
)
from app.db.engine import bind_request_session, new_session, unbind_request_session
from app.services import token_service

__all__ = [
    "AdminUser",
    "CurrentUser",
    "GatewayUser",
    "get_gateway_user",
    "get_identity",
    "normalize_theme",
    "request_db_session",
    "require_admin",
]


def get_gateway_user(
    x_trim_userid: Optional[str] = Header(None, alias="X-Trim-Userid"),
    x_trim_username: Optional[str] = Header(None, alias="X-Trim-Username"),
    x_trim_isadmin: Optional[str] = Header(None, alias="X-Trim-Isadmin"),
    x_trim_theme: Optional[str] = Header(None, alias="X-Trim-Theme"),
    x_fnos_theme: Optional[str] = Header(None, alias="X-Fnos-Theme"),
    x_trim_theme_mode: Optional[str] = Header(None, alias="X-Trim-Theme-Mode"),
) -> GatewayUser:
    """解析逻辑统一在 core.context.gateway_user_from_headers（与权限中间件共用）；
    本依赖保留 Header 形参仅为生成 OpenAPI 文档"""
    return gateway_user_from_headers(
        {
            "x-trim-userid": x_trim_userid or "",
            "x-trim-username": x_trim_username or "",
            "x-trim-isadmin": x_trim_isadmin or "",
            "x-trim-theme": x_trim_theme or "",
            "x-fnos-theme": x_fnos_theme or "",
            "x-trim-theme-mode": x_trim_theme_mode or "",
        }
    )


def get_identity(
    request: Request,
    x_trim_userid: Optional[str] = Header(None, alias="X-Trim-Userid"),
    x_trim_username: Optional[str] = Header(None, alias="X-Trim-Username"),
    x_trim_isadmin: Optional[str] = Header(None, alias="X-Trim-Isadmin"),
    x_trim_theme: Optional[str] = Header(None, alias="X-Trim-Theme"),
    x_fnos_theme: Optional[str] = Header(None, alias="X-Fnos-Theme"),
    x_trim_theme_mode: Optional[str] = Header(None, alias="X-Trim-Theme-Mode"),
) -> GatewayUser:
    """请求身份解析（T-1.2 开放 API）：网关可信头优先，API Token 兜底

    - 网关头齐全时直接采用网关身份（浏览器 / 桌面路径，行为不变）；
    - 权限中间件已验证过 Token 时（scope 暂存身份）直接复用，不二次查库；
    - 无网关头但携带 Token（Authorization: Bearer / X-Api-Token）时验证
      Token 并以其绑定账号为身份——Token 恒为非管理员且只读（写方法与
      管理面由权限中间件拒绝）；无效 Token 明确 401，不静默降级为匿名
      （本兜底同时覆盖无中间件的测试 app 场景）。
    """
    user = get_gateway_user(
        x_trim_userid=x_trim_userid,
        x_trim_username=x_trim_username,
        x_trim_isadmin=x_trim_isadmin,
        x_trim_theme=x_trim_theme,
        x_fnos_theme=x_fnos_theme,
        x_trim_theme_mode=x_trim_theme_mode,
    )
    if user.user_id:
        return user
    stashed = request.scope.get(SCOPE_TOKEN_USER_KEY)
    if stashed is not None:
        return stashed
    token = token_from_headers(
        {
            k: v
            for k, v in request.headers.items()
            if k in ("authorization", "x-api-token")
        }
    )
    if token:
        resolved = token_service.authenticate(token)
        if resolved is None:
            raise UnauthorizedError(TOKEN_INVALID_MSG)
        # Token 恒为只读（写方法与管理面在权限中间件还有第二道拦截）
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            raise PermissionDeniedError(TOKEN_READONLY_MSG)
        return resolved
    return user


def require_admin(user: GatewayUser = Depends(get_gateway_user)) -> GatewayUser:
    """全局影响操作的管理员守卫（备份导出/恢复、全局配置写等）

    网关模式下缺失身份头视为未认证（401，与权限中间件同语义；排障逃生开关
    config.ALLOW_HEADERLESS 开启时恢复旧行为）；网关多账号下非管理员拒绝；
    本地开发、独立部署等无网关场景（user_id 为空串）视为唯一用户放行，
    避免单机用户被锁死。
    权限中间件（core/permissions.py）在路由分发前按策略表做同样拦截，
    本守卫是第二道防线并承担 OpenAPI 文档语义，两层文案保持一致。
    """
    if not user.user_id:
        if IS_FNOS and not ALLOW_HEADERLESS:
            raise UnauthorizedError(UNAUTHENTICATED_MSG)
        return user
    if not user.is_admin:
        raise PermissionDeniedError(ADMIN_ONLY_MSG)
    return user


# 端点签名别名：user: CurrentUser / _: AdminUser，替代重复的 Depends(...) 样板
# CurrentUser 走 get_identity（网关头优先 + Token 兜底）；AdminUser 仍由
# require_admin 守卫——管理员身份只认网关头，Token 永远到不了管理面
CurrentUser = Annotated[GatewayUser, Depends(get_identity)]
AdminUser = Annotated[GatewayUser, Depends(require_admin)]


async def request_db_session() -> Iterator[Session]:
    """请求级数据库会话：router 级依赖（APIRouter(dependencies=[...])）注入后，
    路由内所有 DAO 的 get_db() 复用同一会话，省去逐 DAO 建会话、借还连接的开销

    为什么必须是 async 生成器：FastAPI 对同步依赖在线程池副本中执行，在副本里
    set 的 ContextVar 不会回传请求任务，端点线程看不到；async 依赖直接在事件
    循环的请求任务上下文里执行，set 后同步端点进线程池时继承该上下文，DAO 侧
    才能读到。会话在响应发送完毕后关闭（FastAPI 依赖卸载时机）。

    事务边界不变：仍由每个 get_db 块自行提交/回滚，本依赖只负责会话的创建与
    关闭（异常时回滚兜底，正常路径无挂起写入，close 即无副作用）。
    """
    session = new_session()
    token = bind_request_session(session)
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        unbind_request_session(token)
        session.close()
