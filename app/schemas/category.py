"""消费分类请求/响应模型"""
from pydantic import BaseModel, ConfigDict, Field


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=20, description="分类名称")


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class CategoryDetail(CategoryOut):
    bill_count: int


class CategoryUpdateResult(CategoryOut):
    renamed_bills: int
