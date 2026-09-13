"""请求级公共依赖：从统一网关转发的可信头解析当前飞牛账号

飞牛 fnOS 统一网关在转发前完成登录校验，并附带身份头（见 gateway-registration.md）：
    X-Trim-Userid / X-Trim-Username / X-Trim-Isadmin
本地开发、独立部署等无网关场景没有这些头，归入空串默认账号（历史数据同属该账号）。

主题头（可选、非官方契约）：
    部分网关版本会转发宿主当前主题，键名可能是 X-Trim-Theme / X-Fnos-Theme /
    X-Trim-Theme-Mode 之一。三者都试一遍，取到即用；取不到则返回空串，
    前端会自动回退到 iframe 内直接探测 localStorage / 系统偏好。
"""

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException


@dataclass(frozen=True)
class GatewayUser:
    """当前请求的网关身份（user_id 用于数据归属；无网关头时均为空）"""

    user_id: str = ""
    user_name: str = ""
    is_admin: bool = False
    # 宿主透传的原始主题值（可能为 "10"/"20"/"light"/"dark"，未透传时为空串）
    theme_raw: str = ""


def _normalize_theme(raw: Optional[str]) -> str:
    """把宿主传来的各种主题写法归一为 light / dark；无法识别时返回空串"""
    value = (raw or "").strip().lower()
    if value in ("10", "light", "day", "false", "1"):
        return "light"
    if value in ("20", "dark", "night", "true", "2"):
        return "dark"
    return ""


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
        theme_raw=(
            x_trim_theme or x_fnos_theme or x_trim_theme_mode or ""
        ).strip(),
    )


def normalize_theme(raw: Optional[str]) -> str:
    """对外暴露的主题归一化（供设置接口透传给前端）"""
    return _normalize_theme(raw)


def require_admin(user: GatewayUser = Depends(get_gateway_user)) -> GatewayUser:
    """全局影响操作的管理员守卫（备份导出/恢复、全局配置写等）

    网关多账号模式下仅管理员可用；本地开发、独立部署等无网关场景
    （user_id 为空串）视为唯一用户放行，避免单机用户被锁死。
    """
    if user.user_id and not user.is_admin:
        raise HTTPException(status_code=403, detail="该操作仅限管理员账号")
    return user

