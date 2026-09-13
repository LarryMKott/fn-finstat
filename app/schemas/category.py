"""消费分类请求/响应模型"""

from pydantic import BaseModel, ConfigDict, Field

# 分类名最大长度（schema 校验与 service 校验共用，单一来源）
CATEGORY_NAME_MAX_LENGTH = 20


class CategoryCreate(BaseModel):
    """新增/重命名分类请求体（重命名复用同一模型）"""

    name: str = Field(
        ...,
        min_length=1,
        max_length=CATEGORY_NAME_MAX_LENGTH,
        description="分类名称",
    )


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


class CategoryDeleteResult(CategoryOut):
    """删除结果：moved_bills 为归入「其他」的流水条数"""

    moved_bills: int
