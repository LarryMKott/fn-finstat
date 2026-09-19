"""月度预算接口的数据模型"""

from typing import Optional

from pydantic import BaseModel, Field


class BudgetUpsert(BaseModel):
    """新增/修改预算：category 为空串表示整月总预算"""

    month: str = Field(..., description="预算月份，如 2026-09")
    category: str = Field("", max_length=64, description="消费分类名；空 = 总预算")
    amount: float = Field(..., gt=0, description="预算金额（元）")
    ledger_id: Optional[int] = Field(
        None, ge=1, description="账本 id（T-7.1）；不传落到默认账本"
    )


class FamilyBudgetUpsert(BaseModel):
    """新增/修改家庭预算（T-7.3）：家庭口径不按账本维度，覆盖全体成员支出"""

    month: str = Field(..., description="预算月份，如 2026-09")
    category: str = Field("", max_length=64, description="消费分类名；空 = 家庭总预算")
    amount: float = Field(..., gt=0, description="预算金额（元）")


class BudgetProgress(BaseModel):
    """单条预算进度：budget 预算额，expense 当月该类实际支出，remaining 预算-支出"""

    id: int
    category: str = Field(description="空串表示总预算")
    budget: float
    expense: float
    remaining: float


class BudgetOverview(BaseModel):
    """某月预算总览：total_budget = 总预算行的金额（无总预算行时为分类预算之和）"""

    month: str
    total_budget: float
    total_expense: float
    items: list[BudgetProgress]
