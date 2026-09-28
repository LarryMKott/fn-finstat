"""消费分类请求/响应模型"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

# 分类名最大长度（schema 校验与 service 校验共用，单一来源）
CATEGORY_NAME_MAX_LENGTH = 20


class CategoryCreate(BaseModel):
    """新增/重命名分类请求体（重命名复用同一模型）

    parent_id 仅供新增使用：非空表示创建为该分类的子分类（两级为限）；
    重命名接口忽略本字段（层级不支持移动）。
    """

    name: str = Field(
        ...,
        min_length=1,
        max_length=CATEGORY_NAME_MAX_LENGTH,
        description="分类名称",
    )
    parent_id: Optional[int] = Field(
        None, ge=1, description="父分类 id（创建子分类时传入；不传为顶层）"
    )


class CategoryOut(BaseModel):
    """分类基础信息（children 为子分类 id 列表，扁平列表即树形展开）"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    parent_id: Optional[int] = None
    source: str = "manual"
    children: list[int] = []


class CategoryTreeNode(BaseModel):
    """分类树节点：children 嵌套输出；bill_count / expense_total 为含子孙的 rollup"""

    id: int
    name: str
    parent_id: Optional[int] = None
    source: str = "manual"
    bill_count: int = 0
    expense_total: float = 0
    children: list["CategoryTreeNode"] = []


class CategoryDetail(CategoryOut):
    """分类详情：附当前账号在该分类下的流水条数"""

    bill_count: int


class CategoryUpdateResult(CategoryOut):
    """重命名结果：renamed_bills 为同步更名的流水条数"""

    renamed_bills: int


class CategoryDeleteResult(CategoryOut):
    """删除结果：moved_bills 为归入「其他」的流水条数"""

    moved_bills: int


# ---- 分类关键词（v1.1 CAP-1）----


class KeywordOut(BaseModel):
    """分类关键词条目"""

    id: int
    category_id: int
    keyword: str
    source: str = "manual"
    enabled: bool = True
    created_at: float = 0


class KeywordBatchCreate(BaseModel):
    """批量新增关键词请求（支持人工粘贴与 AI apply 两条入口）"""

    keywords: list[str] = Field(
        ..., min_length=1, max_length=100, description="关键词列表（批量）"
    )


class KeywordToggle(BaseModel):
    """关键词停用/启用请求"""

    enabled: bool


class KeywordMutateResult(BaseModel):
    """批量新增结果：added 实际入库；dropped 清洗剔除（非法词）；
    duplicated 与既有词/批内重复而跳过"""

    added: int
    dropped: int = 0
    duplicated: int = 0
