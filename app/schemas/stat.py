"""统计报表响应模型"""
from pydantic import BaseModel


class StatSummary(BaseModel):
    income: float
    expense: float
    net: float


class MonthPoint(BaseModel):
    month: str
    income: float
    expense: float


class PieItem(BaseModel):
    name: str
    value: float


class MerchantItem(BaseModel):
    merchant: str
    amount: float
    count: int
