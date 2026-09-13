"""请求上下文对象：当前飞牛账号身份（原 app/api/deps.py，下沉到 core 供服务层引用）

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


@dataclass(frozen=True)
class GatewayUser:
    """当前请求的网关身份（user_id 用于数据归属；无网关头时均为空）"""

    user_id: str = ""
    user_name: str = ""
    is_admin: bool = False
    # 宿主透传的原始主题值（可能为 "10"/"20"/"light"/"dark"，未透传时为空串）
    theme_raw: str = ""


def normalize_theme(raw: Optional[str]) -> str:
    """把宿主传来的各种主题写法归一为 light / dark；无法识别时返回空串"""
    value = (raw or "").strip().lower()
    if value in ("10", "light", "day", "false", "1"):
        return "light"
    if value in ("20", "dark", "night", "true", "2"):
        return "dark"
    return ""
