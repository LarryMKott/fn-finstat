"""通用响应模型"""
from typing import Generic, List, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    total: int
    page: int
    page_size: int
    items: List[T]
