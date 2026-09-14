"""HTTP 中间件：请求观测（请求 ID / 耗时 / 慢请求日志）与安全响应头

均为纯 ASGI 中间件（非 BaseHTTPMiddleware）：不引入额外的任务与流转发开销，
也不会吞掉下游异常；在响应 start 报文阶段补写响应头，对静态资源、文件下载
与统一响应体一视同仁。

刻意不设置 X-Frame-Options / frame-ancestors：本应用由飞牛 fnOS 桌面以 iframe
内嵌打开，禁止被嵌入会直接白屏。
"""

import re
import time
import uuid
import logging

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import request_id_var

logger = logging.getLogger(__name__)

# 超过该耗时的请求记 INFO（设置页「运行日志」可查慢接口）；5xx 无论快慢都记 WARNING
SLOW_REQUEST_MS = 1000

# 信任上游（飞牛网关/反代）传入的请求 ID 做链路串联，但只接受无害字符，防响应头注入
_REQUEST_ID_RE = re.compile(r"^[0-9a-zA-Z_-]{1,64}$")

# 请求 ID 在 ASGI scope 里的存放键：最外层 ServerErrorMiddleware 的 500 处理器
# （core/handlers.py）运行在本中间件之外、ContextVar 复位之后，只能经 scope 读取
REQUEST_ID_SCOPE_KEY = "fn_request_id"


def _resolve_request_id(scope: Scope) -> str:
    for key, value in scope.get("headers") or []:
        if key == b"x-request-id":
            raw = value.decode("ascii", errors="ignore").strip()
            if _REQUEST_ID_RE.fullmatch(raw):
                return raw
            break
    return uuid.uuid4().hex[:16]


class ObservabilityMiddleware:
    """请求 ID 贯穿 + 耗时响应头 + 慢请求/5xx 日志

    - X-Request-ID：上游有则沿用，无则生成；写回响应头，并同步进 ContextVar，
      供异常处理器（core/handlers.py）把未处理异常日志与请求关联
    - X-Process-Time-Ms：响应开始时已耗时的毫秒数
    - 5xx 记 WARNING、超过 SLOW_REQUEST_MS 记 INFO（静态资源大文件等长响应
      也会计入，但阈值过滤后不会刷屏）
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _resolve_request_id(scope)
        token = request_id_var.set(request_id)
        scope[REQUEST_ID_SCOPE_KEY] = request_id
        started = time.perf_counter()
        status = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(raw=message["headers"])
                headers["X-Request-ID"] = request_id
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                headers["X-Process-Time-Ms"] = str(elapsed_ms)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # 未处理异常：统一转 500 的处理器挂在最外层 ServerErrorMiddleware 上，
            # 响应不经过本中间件（拿不到状态码），在此先记一条带请求 ID 的 500 日志
            logger.warning(
                "HTTP %s %s -> 500（未处理异常），请求ID %s",
                scope.get("method", "-"),
                scope.get("path", "-"),
                request_id,
            )
            raise
        finally:
            request_id_var.reset(token)
            self._log(scope, status, request_id, (time.perf_counter() - started) * 1000)

    def _log(
        self, scope: Scope, status: int, request_id: str, elapsed_ms: float
    ) -> None:
        rounded = int(elapsed_ms)
        if status >= 500:
            logger.warning(
                "HTTP %s %s -> %d，耗时 %dms，请求ID %s",
                scope.get("method", "-"),
                scope.get("path", "-"),
                status,
                rounded,
                request_id,
            )
        elif elapsed_ms >= SLOW_REQUEST_MS:
            logger.info(
                "慢请求：HTTP %s %s -> %d，耗时 %dms，请求ID %s",
                scope.get("method", "-"),
                scope.get("path", "-"),
                status,
                rounded,
                request_id,
            )


# 安全响应头（全部响应统一补写；不含 frame 类头，原因见模块 docstring）
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class SecurityHeadersMiddleware:
    """为全部 HTTP 响应补写安全响应头（已存在的不覆盖）"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])
                for name, value in SECURITY_HEADERS.items():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_wrapper)


def add_app_middlewares(app: FastAPI) -> None:
    """按 外层观测 → 中层安全头 → 内层压缩 的洋葱顺序注册

    add_middleware 后添加者在外层：观测中间件在最外层才能计量完整耗时，
    并让请求 ID 覆盖含 GZip 在内的全部下游处理。
    """
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(ObservabilityMiddleware)
