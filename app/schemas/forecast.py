"""预测与建议接口的响应模型（T-6.4）：口径字段全部随响应回传，前端原样展示"""

from typing import Optional

from pydantic import BaseModel, Field


class SuggestWindow(BaseModel):
    """预算建议观察窗口（目标月前 6 个完整自然月）"""

    start: str
    end: str
    months: list[str]


class SuggestOutlier(BaseModel):
    """被剔除的一次性大额流水"""

    tx_time: str
    merchant: str
    amount: float


class BudgetSuggestion(BaseModel):
    """单条预算建议：suggested 为建议值，low/high 为建议区间，均基于月度中位数"""

    category: str
    suggested: float
    low: float
    high: float
    months_used: int
    median: float
    current_budget: Optional[float] = None
    excluded_outliers: list[SuggestOutlier]


class BudgetSuggestions(BaseModel):
    """预算建议：采纳由前端调既有 PUT /api/budget 写入，本接口只读"""

    month: str
    window: SuggestWindow
    suggestions: list[BudgetSuggestion]
    notes: list[str]


class FixedItem(BaseModel):
    """固定项（订阅 / 房租 / 工资类）：key = "tx_type:商户"，供排除重算引用"""

    key: str
    merchant: str
    tx_type: str
    monthly_amount: float
    day_of_month: int
    months_hit: int
    monthly_totals: dict[str, float]


class VariableCaliber(BaseModel):
    """可变支出口径：P50 非零月中位数、P90 最差月，日均按 days_per_month 折算"""

    p50_monthly: float
    p90_monthly: float
    monthly_totals: list[dict]
    days_per_month: int


class ForecastPoint(BaseModel):
    """未来某日的两条余额线：p50 预期 / p90 悲观"""

    date: str
    p50: float
    p90: float


class CashFlowForecast(BaseModel):
    """现金流预测：start_source 标明起点余额口径（asset_snapshot / bills_net）"""

    today: str
    horizon_days: int
    window: SuggestWindow
    start_balance: float
    start_source: str
    snapshot_date: Optional[str] = None
    fixed_items: list[FixedItem]
    excluded_items: list[FixedItem]
    variable: VariableCaliber
    points: list[ForecastPoint]
    notes: list[str] = Field(default_factory=list, description="口径说明，前端原样展示")


class WhatIfAdjustment(BaseModel):
    """What-if 单条调整：把某分类的月支出调整到目标值（0 = 砍掉，可高于基线模拟加码）"""

    category: str = Field(..., min_length=1, max_length=64, description="消费分类名")
    monthly_amount: float = Field(
        ..., ge=0, le=1_000_000, description="目标月支出（元）"
    )


class WhatIfRequest(BaseModel):
    """What-if 情景请求：调整清单 + 模拟月数；只读计算，不落库"""

    adjustments: list[WhatIfAdjustment] = Field(
        ..., min_length=1, max_length=10, description="调整清单（最多 10 个分类）"
    )
    months: int = Field(12, ge=1, le=36, description="模拟月数")
