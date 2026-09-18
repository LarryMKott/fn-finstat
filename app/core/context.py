"""请求上下文对象：当前飞牛账号身份（原 app/api/deps.py，下沉到 core 供服务层引用）

飞牛 fnOS 统一网关在转发前完成登录校验，并附带身份头（见 gateway-registration.md）：
    X-Trim-Userid / X-Trim-Username / X-Trim-Isadmin
本地开发、独立部署等无网关场景没有这些头，归入空串默认账号（历史数据同属该账号）。

主题头（可选、非官方契约）：
    部分网关版本会转发宿主当前主题，键名可能是 X-Trim-Theme / X-Fnos-Theme /
    X-Trim-Theme-Mode 之一。三者都试一遍，取到即用；取不到则返回空串，
    前端会自动回退到 iframe 内直接探测 localStorage / 系统偏好。
"""

from collections.abc import Mapping
from contextvars import ContextVar
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


def gateway_user_from_headers(headers: Mapping[str, str]) -> GatewayUser:
    """从请求头构造网关身份；api.deps 依赖与权限中间件共用，保证解析唯一

    键为小写头名，值可为 None（视为未透传）。
    """

    def raw(name: str) -> str:
        return headers.get(name) or ""

    return GatewayUser(
        user_id=raw("x-trim-userid").strip(),
        user_name=raw("x-trim-username").strip(),
        is_admin=raw("x-trim-isadmin").strip().lower() == "true",
        # 主题：先按官方/约定键的顺序取原始值（空串视为未透传），最后统一 strip
        theme_raw=(
            raw("x-trim-theme") or raw("x-fnos-theme") or raw("x-trim-theme-mode")
        ).strip(),
    )


def normalize_theme(raw: Optional[str]) -> str:
    """把宿主传来的各种主题写法归一为 light / dark；无法识别时返回空串"""
    value = (raw or "").strip().lower()
    if value in ("10", "light", "day", "false", "1"):
        return "light"
    if value in ("20", "dark", "night", "true", "2"):
        return "dark"
    return ""


# 请求 ID（core/middleware.py 注入）：同任务内任意层（含异常处理器、线程池中的
# 端点）可读取，用于把日志行与具体请求关联；无请求上下文（后台线程）为空串
request_id_var: ContextVar[str] = ContextVar("fn_request_id", default="")

# 请求 ID 在 ASGI scope 里的存放键：最外层 ServerErrorMiddleware 的 500 处理器
# （core/handlers.py）运行在观测中间件之外、ContextVar 复位之后，只能经 scope
# 读取。定义在本模块（core 最底层），供中间件与异常处理器共用，避免
# handlers 反向依赖 middleware。
REQUEST_ID_SCOPE_KEY = "fn_request_id"

# 安全响应头（全部响应统一补写；不含 frame 类头：本应用由飞牛 fnOS 桌面以
# iframe 内嵌打开，禁止被嵌入会直接白屏）。与 REQUEST_ID_SCOPE_KEY 同理
# 放本模块，500 兜底响应（core/handlers.py）与中间件共用一份。
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def current_request_id() -> str:
    """当前请求 ID；后台线程/启动期无请求上下文时返回空串"""
    return request_id_var.get()
