"""通用响应模型"""

from typing import Generic, List, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    """分页响应通用包装，items 元素类型由各路由的 response_model 指定"""

    total: int
    page: int
    page_size: int
    items: List[T]
