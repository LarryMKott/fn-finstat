"""HTTP 中间件：来源校验、请求观测（请求 ID / 耗时 / 慢请求日志）与安全响应头

均为纯 ASGI 中间件（非 BaseHTTPMiddleware）：不引入额外的任务与流转发开销，
也不会吞掉下游异常；在响应 start 报文阶段补写响应头，对静态资源、文件下载
与统一响应体一视同仁。

刻意不设置 X-Frame-Options / frame-ancestors：本应用由飞牛 fnOS 桌面以 iframe
内嵌打开，禁止被嵌入会直接白屏。
"""

import logging
import re
import time
import uuid
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import ALLOWED_HOSTS, HOST_IS_WILDCARD, IS_FNOS
from app.core.context import REQUEST_ID_SCOPE_KEY, SECURITY_HEADERS, request_id_var
from app.core.errors import ErrorCode
from app.core.permissions import PermissionMiddleware

logger = logging.getLogger(__name__)

# 超过该耗时的请求记 INFO（设置页「运行日志」可查慢接口）；5xx 无论快慢都记 WARNING
SLOW_REQUEST_MS = 1000

# 信任上游（飞牛网关/反代）传入的请求 ID 做链路串联，但只接受无害字符，防响应头注入
_REQUEST_ID_RE = re.compile(r"^[0-9a-zA-Z_-]{1,64}$")


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


# ---- 独立部署的请求来源校验（SourceGuardMiddleware）----

# 仅这些方法做 Origin 同源校验：跨站表单/自动提交只能发出 GET/POST，
# 但 PUT/DELETE/PATCH 一并覆盖，避免将来新增写方法时漏防
_WRITE_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})

UNTRUSTED_SOURCE_MSG = "请求来源不受信任，已拒绝访问"


def _authority_of(scheme: str, netloc: str) -> tuple[str, int] | None:
    """从 Host 头或 Origin 的 authority 部分取 (主机名小写, 端口)

    端口缺省按对应 scheme 的默认端口补齐；IPv6 字面量（[::1]:8090）由
    urlsplit 正确拆解。解析失败（非法端口等）返回 None 交由调用方拒绝/放行。
    """
    try:
        parts = urlsplit(f"//{netloc}") if netloc else None
        host = (parts.hostname if parts else "") or ""
        port = parts.port if parts else None
    except ValueError:
        return None
    host = host.lower()
    if not host:
        return None
    return host, port if port is not None else (443 if scheme == "https" else 80)


def _origin_same_authority(origin: str, request_scheme: str, host_header: str) -> bool:
    """Origin 是否与请求自身 Host:端口 同源（Origin 缺失由调用方先行放行）

    忽略 scheme 只比 authority：独立部署允许前置 https 反代（此时 scope.scheme
    仍是 http），按 scheme 比较会误杀合法请求；跨站伪造（evil.com）在 authority
    上必然不同源，同样被拦。DNS rebinding 场景下 Origin 与 Host 会同时变成攻击
    域名从而绕过本校验，该场景由 Host 白名单负责（见 config.ALLOWED_HOSTS）。
    """
    try:
        parts = urlsplit(origin)
        if parts.scheme not in ("http", "https"):
            return False
        o_host = (parts.hostname or "").lower()
        if not o_host:
            return False
        o_port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return False
    req = _authority_of(request_scheme, host_header)
    return req is not None and (o_host, o_port) == req


class SourceGuardMiddleware:
    """独立部署（非 fnOS 网关模式）的请求来源校验，防两类浏览器侧攻击：

    1. Host 白名单（全部请求）：默认只绑回环地址时，恶意页面可经 DNS rebinding
       把自己的域名解析到 127.0.0.1 绕过同源策略读走接口数据；Host 不在
       config.ALLOWED_HOSTS 即 403。HOST 为通配地址（0.0.0.0）时无法枚举局域网
       访问名，白名单关闭（HOST_IS_WILDCARD），此时启动日志已提示信任面扩大。
    2. 写方法 Origin 同源校验（POST/PUT/DELETE/PATCH）：恶意网站的自动表单
       提交不经过预检即可跨站发出，无 CORS 读取也构成 CSRF；Origin 缺失（curl、
       脚本、Service Worker）放行，非同源 403。

    fnOS 网关模式下整体跳过：网关负责鉴权，且应用被桌面 iframe 跨子域嵌入，
    请求来源天然与应用不同源。被拦请求回统一响应体（code=10002），与权限
    中间件的拦截结构一致，前端无需区分拦截来源。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or IS_FNOS:
            await self.app(scope, receive, send)
            return
        raw: dict[str, str] = {}
        for key, value in scope.get("headers") or []:
            raw.setdefault(key.decode("latin-1").lower(), value.decode("latin-1"))
        host_header = raw.get("host", "")
        method = (scope.get("method") or "GET").upper()

        if not HOST_IS_WILDCARD and host_header:
            host = _authority_of("http", host_header)
            if host is None or host[0] not in ALLOWED_HOSTS:
                await self._reject(scope, receive, send, host_header)
                return

        if method in _WRITE_METHODS:
            origin = raw.get("origin", "")
            if origin and not _origin_same_authority(
                origin, scope.get("scheme") or "http", host_header
            ):
                await self._reject(scope, receive, send, host_header, origin)
                return

        await self.app(scope, receive, send)

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        host: str,
        origin: str = "",
    ) -> None:
        logger.warning(
            "来源拦截：HTTP %s %s（Host=%s，Origin=%s）",
            scope.get("method", "-"),
            scope.get("path", "-"),
            host or "-",
            origin or "-",
        )
        response = JSONResponse(
            status_code=403,
            content={
                "code": ErrorCode.FORBIDDEN,
                "msg": UNTRUSTED_SOURCE_MSG,
                "data": None,
            },
        )
        await response(scope, receive, send)


def add_app_middlewares(app: FastAPI) -> None:
    """按 外层观测 → 安全头 → 来源校验 → 权限门禁 → 压缩 的洋葱顺序注册

    add_middleware 后添加者在外层：观测中间件在最外层才能计量完整耗时（含被
    拦截的 401/403），安全头在所有拦截之外使 401/403 也带 nosniff；来源校验与
    权限门禁位于压缩之内，被拦截请求不进入路由与业务层。
    """
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(PermissionMiddleware)
    app.add_middleware(SourceGuardMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(ObservabilityMiddleware)
