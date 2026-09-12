"""消费分类请求/响应模型"""
from pydantic import BaseModel, ConfigDict, Field


class CategoryCreate(BaseModel):
    """新增/重命名分类请求体（重命名复用同一模型）"""

    name: str = Field(..., min_length=1, max_length=20, description="分类名称")


class CategoryOut(BaseModel):
    """分类基础信息"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class CategoryDetail(CategoryOut):
    """分类详情：附当前账号在该分类下的流水条数"""

    bill_count: int


class CategoryUpdateResult(CategoryOut):
    """重命名结果：renamed_bills 为同步更名的流水条数"""

    renamed_bills: int
