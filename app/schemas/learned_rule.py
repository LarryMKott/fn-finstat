"""分类自学习规则（T-6.3）接口数据模型"""

from typing import Optional

from pydantic import BaseModel, Field


class LearnedRuleOut(BaseModel):
    """规则列表项：active = enabled 且证据达标（hits ≥ CONFIRM_THRESHOLD）"""

    id: int
    pattern: str
    category: str
    hits: int
    enabled: bool
    active: bool
    created_at: float
    updated_at: float


class LearnedRuleUpdate(BaseModel):
    """编辑规则：category / enabled 均可选，None 表示保持不变"""

    category: Optional[str] = Field(None, min_length=1, max_length=64)
    enabled: Optional[bool] = None
