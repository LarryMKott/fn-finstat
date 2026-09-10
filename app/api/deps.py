"""请求级公共依赖：从统一网关转发的可信头解析当前飞牛账号

飞牛 fnOS 统一网关在转发前完成登录校验，并附带身份头（见 gateway-registration.md）：
    X-Trim-Userid / X-Trim-Username / X-Trim-Isadmin
本地开发、独立部署等无网关场景没有这些头，归入空串默认账号（历史数据同属该账号）。
"""
from dataclasses import dataclass
from typing import Optional

from fastapi import Header


@dataclass(frozen=True)
class GatewayUser:
    """当前请求的网关身份（user_id 用于数据归属；无网关头时均为空）"""

    user_id: str = ""
    user_name: str = ""
    is_admin: bool = False


def get_gateway_user(
    x_trim_userid: Optional[str] = Header(None, alias="X-Trim-Userid"),
    x_trim_username: Optional[str] = Header(None, alias="X-Trim-Username"),
    x_trim_isadmin: Optional[str] = Header(None, alias="X-Trim-Isadmin"),
) -> GatewayUser:
    user_id = (x_trim_userid or "").strip()
    return GatewayUser(
        user_id=user_id,
        user_name=(x_trim_username or "").strip(),
        is_admin=(x_trim_isadmin or "").strip().lower() == "true",
    )
