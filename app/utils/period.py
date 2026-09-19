"""月份 / 年度周期工具：格式校验与起止边界计算（预算、统计、AI 报告共用）

约定：
    month 字符串格式为 YYYY-MM（MONTH_PATTERN 校验）
    日期边界一律返回 YYYY-MM-DD 字符串：
        month_range  → (当月第一天, 当月最后一天)，两端按"含当日"语义使用
        month_window → (当月第一天, 次月第一天)，用于"次月 1 号 00:00 前"的
                       datetime 级比较场景（注意 build_criteria 对 10 位日期
                       按含当日处理，不要把它当排他上界传进去）

周期类型扩展（PERIOD_TYPES）：
    month   period_value = "2026-09"
    quarter period_value = "2026-Q1"（Q1=1-3 月、Q2=4-6 月、Q3=7-9 月、Q4=10-12 月）
    half    period_value = "2026-H1"（H1=1-6 月、H2=7-12 月）
    year    period_value = "2026"
所有周期统一以「含当日两端」的 YYYY-MM-DD 表示边界（period_range），便于复用 build_criteria。
"""

import re
from datetime import date, timedelta

from app.core.errors import ValidationError

MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
QUARTER_PATTERN = re.compile(r"^\d{4}-Q[1-4]$")
HALF_PATTERN = re.compile(r"^\d{4}-H[12]$")
YEAR_PATTERN = re.compile(r"^\d{4}$")

PERIOD_TYPES = ("month", "quarter", "half", "year")

# 每个季度对应 (首月, 末月) 的月份序号
_QUARTER_MONTHS = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}
# 每个半年对应 (首月, 末月) 的月份序号
_HALF_MONTHS = {1: (1, 6), 2: (7, 12)}


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
    return shift_month(month, -1)


def shift_month(month: str, offset: int) -> str:
    """月份平移 offset 个月（正数向后、负数向前），跨年自动进退位

    预测回看窗口、自然语言「上 N 个月」等场景共用，避免各处手写回退循环。
    """
    year, mon = parse_month(month)
    total = year * 12 + (mon - 1) + offset
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


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


# ---- 周期类型扩展（月/季/半年/年）----

_PERIOD_PATTERNS = {
    "month": MONTH_PATTERN,
    "quarter": QUARTER_PATTERN,
    "half": HALF_PATTERN,
    "year": YEAR_PATTERN,
}


def valid_period(period_type: str, period_value: str) -> bool:
    """周期标识是否合法（period_type 在 PERIOD_TYPES 中且 period_value 匹配对应正则）"""
    if period_type not in _PERIOD_PATTERNS:
        return False
    return bool(_PERIOD_PATTERNS[period_type].match(period_value or ""))


def parse_period(period_type: str, period_value: str) -> tuple[int, ...]:
    """周期标识 → 整数元组；非法抛 ValidationError

    month   → (year, mon)
    quarter → (year, quarter)
    half    → (year, half)
    year    → (year,)
    """
    if not valid_period(period_type, period_value):
        raise ValidationError(f"无效的周期标识：{period_type}={period_value}")
    year = int(period_value[:4])
    if period_type == "month":
        return year, int(period_value[5:7])
    if period_type == "quarter":
        return year, int(period_value[6])
    if period_type == "half":
        return year, int(period_value[6])
    return (year,)


def period_range(period_type: str, period_value: str) -> tuple[str, str]:
    """周期 → 起止日期（含端点），供 build_criteria 与统计复用

    季度/半年按首末月 month_range 拼装，自动处理闰年与 30/31 天月份。
    """
    parts = parse_period(period_type, period_value)
    if period_type == "month":
        return month_range(period_value)
    if period_type == "quarter":
        year, q = parts
        first_mon, last_mon = _QUARTER_MONTHS[q]
        start = f"{year:04d}-{first_mon:02d}-01"
        _, end = month_range(f"{year:04d}-{last_mon:02d}")
        return start, end
    if period_type == "half":
        year, h = parts
        first_mon, last_mon = _HALF_MONTHS[h]
        start = f"{year:04d}-{first_mon:02d}-01"
        _, end = month_range(f"{year:04d}-{last_mon:02d}")
        return start, end
    # year
    return year_window(parts[0])


def prev_period(period_type: str, period_value: str) -> str:
    """上一周期同维标识（月→上月、季→同年前一季、半年→同年前一半年、年→前一年）"""
    parts = parse_period(period_type, period_value)
    if period_type == "month":
        return prev_month(period_value)
    if period_type == "quarter":
        year, q = parts
        if q == 1:
            return f"{year - 1:04d}-Q4"
        return f"{year:04d}-Q{q - 1}"
    if period_type == "half":
        year, h = parts
        if h == 1:
            return f"{year - 1:04d}-H2"
        return f"{year:04d}-H1"
    # year
    return f"{parts[0] - 1:04d}"


def last_full_months(today: date, count: int) -> list[str]:
    """今日之前最近 count 个完整自然月（YYYY-MM，升序）；跨年正确回退

    「近 N 个月」类统计（预算建议、财务健康评分等）的统一窗口口径。
    """
    year, month = int(f"{today.year:04d}"), today.month
    cur = f"{year:04d}-{month:02d}"
    return [shift_month(cur, -i) for i in range(count, 0, -1)]


def period_label(period_type: str, period_value: str) -> str:
    """周期 → 人类可读标签（用于报告标题与界面展示）"""
    parts = parse_period(period_type, period_value)
    if period_type == "month":
        year, mon = parts
        return f"{year} 年 {mon} 月"
    if period_type == "quarter":
        year, q = parts
        return f"{year} 年 Q{q} 季度"
    if period_type == "half":
        year, h = parts
        return f"{year} 年{'上' if h == 1 else '下'}半年"
    # year
    return f"{parts[0]} 年度"


def default_period_value(period_type: str, now: date | None = None) -> str:
    """当前周期标识（用于「默认分析上一周期」场景；缺省 today）

    - month: 上月（与既有 generate_month_report 默认值一致）
    - quarter / half / year: 上一周期（去年末季 / 去年下半年 / 去年）
    """
    today = now or date.today()
    if period_type == "month":
        return prev_month(f"{today.year:04d}-{today.month:02d}")
    if period_type == "quarter":
        q = (today.month - 1) // 3 + 1
        return prev_period("quarter", f"{today.year:04d}-Q{q}")
    if period_type == "half":
        h = 1 if today.month <= 6 else 2
        return prev_period("half", f"{today.year:04d}-H{h}")
    if period_type == "year":
        return f"{today.year - 1:04d}"
    raise ValidationError(f"未知的周期类型：{period_type}")
