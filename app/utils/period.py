"""月份 / 年度周期工具：格式校验与起止边界计算（预算、统计、AI 报告共用）

约定：
    month 字符串格式为 YYYY-MM（MONTH_PATTERN 校验）
    日期边界一律返回 YYYY-MM-DD 字符串：
        month_range  → (当月第一天, 当月最后一天)，两端按"含当日"语义使用
        month_window → (当月第一天, 次月第一天)，用于"次月 1 号 00:00 前"的
                       datetime 级比较场景（注意 build_criteria 对 10 位日期
                       按含当日处理，不要把它当排他上界传进去）
"""

import re
from datetime import date, timedelta

from app.core.errors import ValidationError

MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def valid_month(month: str) -> bool:
    """月份字符串是否为合法 YYYY-MM（含月份范围 01-12）"""
    return bool(MONTH_PATTERN.match(month or ""))


def parse_month(month: str) -> tuple[int, int]:
    """YYYY-MM → (year, mon)；格式非法抛 ValidationError"""
    if not valid_month(month):
        raise ValidationError(f"无效的月份：{month}")
    return int(month[:4]), int(month[5:7])


def next_month(year: int, mon: int) -> tuple[int, int]:
    """下一个月的 (year, mon)"""
    return (year + 1, 1) if mon == 12 else (year, mon + 1)


def prev_month(month: str) -> str:
    """上一个月的 YYYY-MM"""
    year, mon = parse_month(month)
    if mon == 1:
        return f"{year - 1}-12"
    return f"{year}-{mon - 1:02d}"


def month_range(month: str) -> tuple[str, str]:
    """月份 → 当月起止日期（含端点），供筛选/统计复用"""
    year, mon = parse_month(month)
    next_y, next_m = next_month(year, mon)
    end = date(next_y, next_m, 1) - timedelta(days=1)
    return f"{year:04d}-{mon:02d}-01", end.strftime("%Y-%m-%d")


def month_window(year: int, mon: int) -> tuple[str, str]:
    """年月 → (当月第一天, 次月第一天)"""
    next_y, next_m = next_month(year, mon)
    return f"{year:04d}-{mon:02d}-01", f"{next_y:04d}-{next_m:02d}-01"


def year_window(year: int) -> tuple[str, str]:
    """年份 → (当年第一天, 当年最后一天)（两端含当日）"""
    return f"{year:04d}-01-01", f"{year:04d}-12-31"
