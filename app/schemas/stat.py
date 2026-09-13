"""统计报表响应模型"""

from typing import Optional

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


class DailyPoint(BaseModel):
    """日历热力图单点：date 形如 2026-09-01"""

    date: str
    income: float
    expense: float


class MonthlyCompare(BaseModel):
    """年度对比月度项：月份两位数字，本年/去年收支对比"""

    month: str
    this_income: float
    this_expense: float
    last_income: float
    last_expense: float


class CategoryCompare(BaseModel):
    """年度对比分类项：本年/去年支出对比"""

    category: str
    this_year: float
    last_year: float


class YearComparison(BaseModel):
    """年度对比报表：本年与去年的汇总、逐月与分类对比"""

    year: int
    last_year: int
    this_income: float
    this_expense: float
    last_income: float
    last_expense: float
    monthly: list[MonthlyCompare]
    categories: list[CategoryCompare]


class RegionItem(BaseModel):
    """消费地图省级项：name 为省级行政区全称（与地图 name 一致）"""

    name: str
    value: float
    count: int


class CityRegionItem(BaseModel):
    """消费地图城市项：province 为其所属省级行政区

    coord 为该城市中心点坐标 [经度, 纬度]（GCJ-02），前端据此在地图上打点气泡；
    城市在坐标表中缺失时为 None，前端应跳过打点但保留在排行榜中。
    """

    name: str
    value: float
    count: int
    province: str
    coord: Optional[list[float]] = None


class RegionMap(BaseModel):
    """消费地图数据：城市气泡 + 省级分布 + 识别率

    地域由商户名/备注文本推断而来（账单无地区字段），matched_* 字段用于向
    用户如实说明识别覆盖度，避免地图被误读为完整地理分布。
    """

    provinces: list[RegionItem]
    cities: list[CityRegionItem]
    max_value: float
    total_amount: float
    matched_amount: float
    matched_count: int
    scanned_count: int
    total_count: int
    truncated: bool
    matched_rate: int
