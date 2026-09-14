"""全局异常处理器：把业务异常族与框架异常统一转换为统一响应体

统一响应结构（成功与失败同构）：
    成功  {"code": 0,    "msg": "ok",             "data": <业务数据>}
    失败  {"code": <错误码>, "msg": <用户可读信息>, "data": null}

HTTP 状态码保留语义（400/403/404/422/500），前端同时可用两者判断。
文件下载类端点返回二进制响应，不走本包装。
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.context import current_request_id
from app.core.errors import BizError, ErrorCode
from app.core.middleware import REQUEST_ID_SCOPE_KEY, SECURITY_HEADERS

logger = logging.getLogger(__name__)


def error_body(code: int, message: str) -> dict:
    """统一失败响应体"""
    return {"code": code, "msg": message, "data": None}


def _http_error_code(status: int) -> int:
    """存量 HTTPException 的状态码 → 错误码兜底映射"""
    if status == 403:
        return ErrorCode.FORBIDDEN
    if status == 404:
        return ErrorCode.NOT_FOUND
    if 400 <= status < 500:
        return ErrorCode.BAD_REQUEST
    return ErrorCode.INTERNAL_ERROR


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器（main.app 与测试用 app 共用，保证契约一致）"""

    @app.exception_handler(BizError)
    async def _biz_error_handler(_: Request, exc: BizError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=error_body(exc.code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 请求体/查询参数校验失败：状态码维持 FastAPI 默认 422，body 换成统一结构，
        # 校验错误拼接为可读文本（与前端 api.js 的旧 detail 兼容解析等效）
        parts: list[str] = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
            msg = str(err.get("msg", "") or "")
            parts.append(f"{loc} {msg}".strip() if loc else msg)
        message = "；".join(dict.fromkeys(p for p in parts if p)) or "请求参数不合法"
        return JSONResponse(
            status_code=422,
            content=error_body(ErrorCode.BAD_REQUEST, message),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # 兼容存量/框架内部 HTTPException（未匹配路由 404、静态资源 404 等），转统一结构。
        # 必须注册 Starlette 基类：fastapi.HTTPException 是其子类，只注册子类时
        # 框架对未匹配路由抛出的基类异常按 MRO 匹配不到，会绕过统一响应体
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(_http_error_code(exc.status_code), detail),
        )

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        # 未预期异常：记录完整堆栈（设置页「运行日志」可查，带请求 ID 便于与
        # 观测中间件的访问日志关联），对外只暴露通用文案，不透出 SQLAlchemy /
        # 驱动等内部细节。
        # 本处理器挂在最外层 ServerErrorMiddleware 上、观测中间件之外：响应不会
        # 再经过中间件，观测/安全头须在此补写；请求 ID 经 scope 读取（ContextVar
        # 已被中间件 finally 复位）。
        request_id = str(request.scope.get(REQUEST_ID_SCOPE_KEY) or "")
        logger.exception(
            "未处理异常[请求ID %s]：%s",
            request_id or current_request_id() or "-",
            exc,
        )
        headers = dict(SECURITY_HEADERS)
        if request_id:
            headers["X-Request-ID"] = request_id
        return JSONResponse(
            status_code=500,
            content=error_body(ErrorCode.INTERNAL_ERROR, "服务器内部错误，请稍后重试"),
            headers=headers,
        )
