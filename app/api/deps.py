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

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.context import GatewayUser, normalize_theme
from app.core.errors import PermissionDeniedError
from app.db.engine import bind_request_session, new_session, unbind_request_session

__all__ = [
    "AdminUser",
    "CurrentUser",
    "GatewayUser",
    "get_gateway_user",
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
    user_id = (x_trim_userid or "").strip()
    return GatewayUser(
        user_id=user_id,
        user_name=(x_trim_username or "").strip(),
        is_admin=(x_trim_isadmin or "").strip().lower() == "true",
        theme_raw=(x_trim_theme or x_fnos_theme or x_trim_theme_mode or "").strip(),
    )


def require_admin(user: GatewayUser = Depends(get_gateway_user)) -> GatewayUser:
    """全局影响操作的管理员守卫（备份导出/恢复、全局配置写等）

    网关多账号模式下仅管理员可用；本地开发、独立部署等无网关场景
    （user_id 为空串）视为唯一用户放行，避免单机用户被锁死。
    """
    if user.user_id and not user.is_admin:
        raise PermissionDeniedError("该操作仅限管理员账号")
    return user


# 端点签名别名：user: CurrentUser / _: AdminUser，替代重复的 Depends(...) 样板
CurrentUser = Annotated[GatewayUser, Depends(get_gateway_user)]
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
