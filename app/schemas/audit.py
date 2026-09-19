"""操作审计（T-7.6）的响应模型"""

from pydantic import BaseModel, Field


class AuditLogOut(BaseModel):
    """单条审计：操作人 / 动作 / 对象 / 差异摘要 / 时间"""

    id: int
    user_id: str
    action: str
    entity: str = ""
    entity_id: str | None = None
    summary: str = ""
    created_at: float


class AuditLogPage(BaseModel):
    """审计查询结果（按时间倒序）"""

    total: int
    items: list[AuditLogOut] = Field(default_factory=list)
