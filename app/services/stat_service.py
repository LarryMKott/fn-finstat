"""统计报表业务逻辑（仅统计当前飞牛账号的账单）"""

from typing import Optional

from app.db.dao.stat_dao import StatDAO
from app.utils.amount import round2
from app.utils.city_geo import CITY_CENTERS
from app.utils.period import month_range, year_window
from app.utils.region_matcher import detect_city, detect_region

# 消费地图参与识别的流水条数上限：地域识别是逐条文本推断，需要设防
# 超限时按金额降序截断（DAO 已排序），保证大额流水优先被统计
REGION_SCAN_LIMIT = 20000

# 消费地图城市榜单长度
CITY_TOP_LIMIT = 15


def summary(
    user_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    account: Optional[str] = None,
    tx_type: Optional[str] = None,
) -> dict:
    """收支汇总：income/expense 与结余 net（net = income - expense）"""
    data = StatDAO.summary(user_id, start, end, account, tx_type)
    income = round2(data["income"])
    expense = round2(data["expense"])
    return {"income": income, "expense": expense, "net": round(income - expense, 2)}


def month_trend(
    user_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    account: Optional[str] = None,
    tx_type: Optional[str] = None,
) -> list[dict]:
    """月度收支趋势（按月升序），金额保留 2 位小数"""
    rows = StatDAO.month_trend(user_id, start, end, account, tx_type)
    return [
        {
            "month": r["month"],
            "income": round2(r["income"]),
            "expense": round2(r["expense"]),
        }
        for r in rows
    ]


def category_pie(
    user_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    account: Optional[str] = None,
) -> list[dict]:
    """支出分类占比数据（name 分类名 / value 金额），金额保留 2 位小数"""
    rows = StatDAO.category_pie(user_id, start, end, account)
    return [{"name": r["name"], "value": round2(r["value"])} for r in rows]


def merchant_top(
    user_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    account: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """商户消费 TOP N：amount 消费总额（2 位小数）、count 笔数"""
    rows = StatDAO.merchant_top(user_id, start, end, account, limit)
    return [
        {"merchant": r["merchant"], "amount": round2(r["amount"]), "count": r["count"]}
        for r in rows
    ]


def daily_heatmap(
    user_id: str,
    year: int,
    month: Optional[int] = None,
    account: Optional[str] = None,
) -> list[dict]:
    """按日收支汇总（日历热力图）：默认全年，传 month 时只看某月

    start/end 均为"含当日"的日期边界（build_criteria 对 10 位日期按整日包含处理）。
    """
    if month:
        start, end = month_range(f"{year:04d}-{month:02d}")
    else:
        start, end = year_window(year)
    rows = StatDAO.daily_totals(user_id, start=start, end=end, account=account)
    return [
        {
            "date": r["date"],
            "income": round2(r["income"]),
            "expense": round2(r["expense"]),
        }
        for r in rows
    ]


def region_map(
    user_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    account: Optional[str] = None,
) -> dict:
    """消费地图：按省级行政区聚合支出，并给出城市 TOP 与识别率

    **重要局限（必须如实回传给前端）**：账单里没有地区字段，地域只能从商户名 /
    备注文本推断，因此存在一定比例的「未识别」。返回值中的 unmatched_* 字段用于
    向用户透明展示这一事实，避免地图被误读为完整的地理分布。
    """
    # 多取一行用于判断是否截断；行数上限在 DAO 层流式拉取，大账本不整体物化
    rows = StatDAO.region_rows(
        user_id, start, end, account, max_rows=REGION_SCAN_LIMIT + 1
    )
    truncated = len(rows) > REGION_SCAN_LIMIT
    scanned = rows[:REGION_SCAN_LIMIT]

    provinces: dict[str, dict] = {}
    cities: dict[str, dict] = {}
    matched_amount = 0.0
    total_amount = 0.0
    matched_count = 0

    for row in scanned:
        amount = round2(row["amount"])
        total_amount += amount
        province = detect_region(row["merchant"] or "", row["remark"] or "")
        if province is None:
            continue
        matched_amount += amount
        matched_count += 1

        item = provinces.setdefault(province, {"total": 0.0, "count": 0})
        item["total"] = round(item["total"] + amount, 2)
        item["count"] += 1

        city = detect_city(row["merchant"] or "", row["remark"] or "")
        if city:
            c_item = cities.setdefault(
                city, {"total": 0.0, "count": 0, "province": province}
            )
            c_item["total"] = round(c_item["total"] + amount, 2)
            c_item["count"] += 1

    province_items = sorted(
        (
            {"name": name, "value": data["total"], "count": data["count"]}
            for name, data in provinces.items()
        ),
        key=lambda d: -d["value"],
    )
    city_items = sorted(
        (
            {
                "name": name,
                "value": data["total"],
                "count": data["count"],
                "province": data["province"],
                # 坐标仅用于前端气泡打点；缺失时前端跳过打点但保留榜单
                "coord": CITY_CENTERS.get(name),
            }
            for name, data in cities.items()
        ),
        key=lambda d: -d["value"],
    )[:CITY_TOP_LIMIT]

    matched_total = round2(matched_amount)
    return {
        "provinces": province_items,
        "cities": city_items,
        "max_value": province_items[0]["value"] if province_items else 0.0,
        "total_amount": round2(total_amount),
        "matched_amount": matched_total,
        "matched_count": matched_count,
        "scanned_count": len(scanned),
        "total_count": len(rows),
        "truncated": truncated,
        # 识别率按金额口径（比条数口径更贴近「看钱花在哪」的诉求）
        "matched_rate": (
            round(matched_total / total_amount * 100) if total_amount > 0 else 0
        ),
    }


def year_comparison(user_id: str, year: int, account: Optional[str] = None) -> dict:
    """年度对比：今年 vs 去年的月度收支、年度汇总与分类支出对比"""
    last_year = year - 1

    def _by_month(target_year: int) -> dict[str, dict]:
        start, end = year_window(target_year)
        rows = StatDAO.month_trend(user_id, start=start, end=end, account=account)
        return {r["month"]: r for r in rows}

    this_map, last_map = _by_month(year), _by_month(last_year)
    monthly = []
    for m in range(1, 13):
        key = f"{year:04d}-{m:02d}"
        this_row = this_map.get(key, {})
        last_row = last_map.get(f"{last_year:04d}-{m:02d}", {})
        monthly.append(
            {
                "month": f"{m:02d}",
                "this_income": round2(this_row.get("income", 0)),
                "this_expense": round2(this_row.get("expense", 0)),
                "last_income": round2(last_row.get("income", 0)),
                "last_expense": round2(last_row.get("expense", 0)),
            }
        )

    this_start, this_end = year_window(year)
    last_start, last_end = year_window(last_year)
    this_summary = StatDAO.summary(user_id, this_start, this_end, account)
    last_summary = StatDAO.summary(user_id, last_start, last_end, account)

    def _category_map(target_year: int) -> dict[str, float]:
        start, end = year_window(target_year)
        return {
            r["name"]: round2(r["value"])
            for r in StatDAO.category_pie(
                user_id, start=start, end=end, account=account
            )
        }

    this_cats, last_cats = _category_map(year), _category_map(last_year)
    categories = [
        {
            "category": name,
            "this_year": this_cats.get(name, 0.0),
            "last_year": last_cats.get(name, 0.0),
        }
        for name in sorted(
            set(this_cats) | set(last_cats), key=lambda n: -this_cats.get(n, 0)
        )
    ]

    return {
        "year": year,
        "last_year": last_year,
        "this_income": round2(this_summary["income"]),
        "this_expense": round2(this_summary["expense"]),
        "last_income": round2(last_summary["income"]),
        "last_expense": round2(last_summary["expense"]),
        "monthly": monthly,
        "categories": categories,
    }
