"""储蓄目标（T-1.4）的请求 / 响应模型"""

from typing import Optional

from pydantic import BaseModel, Field


class SavingsGoalCreate(BaseModel):
    """新建储蓄目标：进度从创建日起由账单结余自动计入"""

    name: str = Field(..., min_length=1, max_length=64, description="目标名称")
    target_amount: float = Field(..., gt=0, description="目标金额（元）")
    target_date: Optional[str] = Field(None, description="目标日期 YYYY-MM-DD（可选）")
    note: str = Field("", max_length=255, description="备注")


class SavingsGoalUpdate(BaseModel):
    """更新储蓄目标：字段均可选，仅传入的字段生效（起始日不可改）"""

    name: Optional[str] = Field(None, min_length=1, max_length=64)
    target_amount: Optional[float] = Field(None, gt=0)
    target_date: Optional[str] = None
    note: Optional[str] = Field(None, max_length=255)


class SavingsGoalProgress(BaseModel):
    """结余自动计入的进度：saved 为起始日以来累计净结余（收入 − 支出）"""

    saved: float
    remaining: float
    pct: int
    done: bool
    months_left: Optional[float] = None
    per_month_needed: Optional[float] = None


class SavingsGoalOut(BaseModel):
    """储蓄目标（含进度）"""

    id: int
    name: str
    target_amount: float
    start_date: str
    target_date: Optional[str] = None
    note: str = ""
    created_at: float
    saved: float = 0
    remaining: float = 0
    pct: int = 0
    done: bool = False
    months_left: Optional[float] = None
    per_month_needed: Optional[float] = None


class SavingsGoalList(BaseModel):
    """储蓄目标列表"""

    items: list[SavingsGoalOut] = Field(default_factory=list)
