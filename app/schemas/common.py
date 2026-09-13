"""通用响应模型：统一包装结构与分页"""

from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一响应包装（成功与失败同构，见 app/core/handlers.py）

    成功  {"code": 0, "msg": "ok", "data": <业务数据>}
    失败  {"code": <错误码>, "msg": <用户可读信息>, "data": null}
    错误码取值见 app/core/errors.py::ErrorCode；文件下载端点返回二进制，不包装。
    """

    code: int = 0
    msg: str = "ok"
    data: Optional[T] = None


def ok(data: Any = None) -> dict:
    """路由层统一成功返回体（data 为 None 时仍输出 "data": null）"""
    return {"code": 0, "msg": "ok", "data": data}


class PageResult(BaseModel, Generic[T]):
    """分页响应通用包装，items 元素类型由各路由的 response_model 指定"""

    total: int
    page: int
    page_size: int
    items: List[T]
