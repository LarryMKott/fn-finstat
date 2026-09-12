"""统计报表响应模型"""
from pydantic import BaseModel


class StatSummary(BaseModel):
    """收支汇总：net = income - expense"""

    income: float
    expense: float
    net: float


class MonthPoint(BaseModel):
    """月度趋势单点，month 形如 2024-01"""

    month: str
    income: float
    expense: float


class PieItem(BaseModel):
    """分类饼图单项：name 分类名，value 支出金额"""

    name: str
    value: float


class MerchantItem(BaseModel):
    """商户消费排行单项：amount 消费总额，count 笔数"""

    merchant: str
    amount: float
    count: int
