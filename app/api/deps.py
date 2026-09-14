"""请求级公共依赖：网关身份解析与管理员守卫

身份对象（GatewayUser）与主题归一化定义在 app/core/context.py（服务层可引用），
本模块只提供 FastAPI 依赖注入函数（路由层专用）。

⚠️ 身份头的可信前提：X-Trim-* 头由飞牛网关注入，本模块**无条件信任**，不做来源
校验。因此应用绝不能直接暴露在不可信网络：
- fnOS 网关模式：cmd/main 以 `uvicorn --uds` 启动，只绑 Unix Socket 不监听 TCP；
- 独立部署/本地运行：config.HOST 默认绑 127.0.0.1（详见 config.py 的 HOST 注释）。
任一前提下被绕过，攻击者伪造两个头即可提权为管理员。
"""

from typing import Optional

from fastapi import Depends, Header

from app.core.context import GatewayUser, normalize_theme
from app.core.errors import PermissionDeniedError

__all__ = ["GatewayUser", "get_gateway_user", "normalize_theme", "require_admin"]


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
