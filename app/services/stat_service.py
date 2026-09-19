"""统计报表业务逻辑（仅统计当前飞牛账号的账单，T-7.1 起支持账本维度）

ledger_id 为 None 表示不按账本过滤（旧调用行为不变），由 DAO 层强制注入。
"""

from typing import Optional

from app.db.dao.asset_dao import AssetDAO
from app.db.dao.stat_dao import StatDAO
from app.utils.amount import round2
from app.utils.city_geo import CITY_CENTERS
from app.utils.period import last_full_months, month_range, year_window
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
    ledger_id: Optional[int] = None,
) -> dict:
    """收支汇总：income/expense 与结余 net（net = income - expense）"""
    data = StatDAO.summary(user_id, start, end, account, tx_type, ledger_id)
    income = round2(data["income"])
    expense = round2(data["expense"])
    return {"income": income, "expense": expense, "net": round(income - expense, 2)}


def month_trend(
    user_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    account: Optional[str] = None,
    tx_type: Optional[str] = None,
    ledger_id: Optional[int] = None,
) -> list[dict]:
    """月度收支趋势（按月升序），金额保留 2 位小数"""
    rows = StatDAO.month_trend(user_id, start, end, account, tx_type, ledger_id)
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
    ledger_id: Optional[int] = None,
) -> list[dict]:
    """支出分类占比数据（name 分类名 / value 金额），金额保留 2 位小数"""
    rows = StatDAO.category_pie(user_id, start, end, account, ledger_id)
    return [{"name": r["name"], "value": round2(r["value"])} for r in rows]


def merchant_top(
    user_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    account: Optional[str] = None,
    limit: int = 10,
    ledger_id: Optional[int] = None,
) -> list[dict]:
    """商户消费 TOP N：amount 消费总额（2 位小数）、count 笔数"""
    rows = StatDAO.merchant_top(user_id, start, end, account, limit, ledger_id)
    return [
        {"merchant": r["merchant"], "amount": round2(r["amount"]), "count": r["count"]}
        for r in rows
    ]


def daily_heatmap(
    user_id: str,
    year: int,
    month: Optional[int] = None,
    account: Optional[str] = None,
    ledger_id: Optional[int] = None,
) -> list[dict]:
    """按日收支汇总（日历热力图）：默认全年，传 month 时只看某月

    start/end 均为"含当日"的日期边界（build_criteria 对 10 位日期按整日包含处理）。
    """
    if month:
        start, end = month_range(f"{year:04d}-{month:02d}")
    else:
        start, end = year_window(year)
    rows = StatDAO.daily_totals(
        user_id, start=start, end=end, account=account, ledger_id=ledger_id
    )
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
    ledger_id: Optional[int] = None,
) -> dict:
    """消费地图：按省级行政区聚合支出，并给出城市 TOP 与识别率

    **重要局限（必须如实回传给前端）**：账单里没有地区字段，地域只能从商户名 /
    备注文本推断，因此存在一定比例的「未识别」。返回值中的 unmatched_* 字段用于
    向用户透明展示这一事实，避免地图被误读为完整的地理分布。
    """
    # 多取一行用于判断是否截断；行数上限在 DAO 层流式拉取，大账本不整体物化
    rows = StatDAO.region_rows(
        user_id,
        start,
        end,
        account,
        max_rows=REGION_SCAN_LIMIT + 1,
        ledger_id=ledger_id,
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


def year_comparison(
    user_id: str,
    year: int,
    account: Optional[str] = None,
    ledger_id: Optional[int] = None,
) -> dict:
    """年度对比：今年 vs 去年的月度收支、年度汇总与分类支出对比"""
    last_year = year - 1

    def _by_month(target_year: int) -> dict[str, dict]:
        start, end = year_window(target_year)
        rows = StatDAO.month_trend(
            user_id, start=start, end=end, account=account, ledger_id=ledger_id
        )
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
    this_summary = StatDAO.summary(
        user_id, this_start, this_end, account, None, ledger_id
    )
    last_summary = StatDAO.summary(
        user_id, last_start, last_end, account, None, ledger_id
    )

    def _category_map(target_year: int) -> dict[str, float]:
        start, end = year_window(target_year)
        return {
            r["name"]: round2(r["value"])
            for r in StatDAO.category_pie(
                user_id, start=start, end=end, account=account, ledger_id=ledger_id
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


# ---- 财务健康评分（T-1.3）：口径在响应内完全公开，见 items[].formula ----

HEALTH_WINDOW_MONTHS = 6  # 评估窗口：近 N 个完整自然月
# 各分项权重（合计 1.0；某分项数据缺失时按剩余权重归一）
HEALTH_WEIGHTS = {"savings": 0.4, "debt": 0.3, "emergency": 0.3}


def _clamp_score(value: float) -> float:
    return round2(max(0.0, min(value, 100.0)))


def health_score(user_id: str, today=None) -> dict:
    """财务健康评分（只读，不落库）：储蓄率 / 负债率 / 应急金月数三分项

    口径（响应 items[].formula 同步下发，界面完全公开）：
    - 储蓄率 =（月均收入 − 月均支出）÷ 月均收入，取近 6 个完整自然月，
      ≥20% 得满分、0% 及以下 0 分，线性内插
    - 负债率 = 最新资产快照的负债合计 ÷（资产合计 + 负债合计），
      ≤30% 满分、≥70% 0 分，线性内插
    - 应急金月数 = 最新资产快照的资产合计 ÷ 月均支出，≥6 个月满分、
      0 个月 0 分，线性内插
    缺数据的分项不计分，总分按剩余权重归一（如实提示，不造假数字）。
    """
    from datetime import date as _date

    today = today or _date.today()
    months = last_full_months(today, HEALTH_WINDOW_MONTHS)
    start, end = month_range(months[0])[0], month_range(months[-1])[1]

    # 月均收入 / 支出：完整月均值（未记账月计 0，不剔除）
    trend = StatDAO.month_trend(user_id, start=start, end=end)
    by_month = {row["month"]: row for row in trend}
    incomes = [round2(by_month.get(m, {}).get("income", 0)) for m in months]
    expenses = [round2(by_month.get(m, {}).get("expense", 0)) for m in months]
    avg_income = round2(sum(incomes) / len(months))
    avg_expense = round2(sum(expenses) / len(months))

    window_label = f"{months[0]} ~ {months[-1]}"
    items = []

    savings_rate = None
    savings_score = None
    if avg_income > 0:
        # 输出百分数（0.6 → 60.0），与 formula 的「≥20%」口径一致
        savings_rate = round2((avg_income - avg_expense) / avg_income * 100)
        savings_score = _clamp_score(savings_rate / 20 * 100)
    items.append(
        {
            "key": "savings",
            "label": "储蓄率",
            "value": savings_rate,
            "unit": "",
            "score": savings_score,
            "weight": HEALTH_WEIGHTS["savings"],
            "available": avg_income > 0,
            "hint": None if avg_income > 0 else "近 6 个完整月没有收入记录，无法评估",
            "formula": f"储蓄率 =（月均收入 {avg_income} − 月均支出 {avg_expense}）÷ 月均收入，"
            f"取近 6 个完整月（{window_label}）；≥20% 满分、≤0% 零分，线性内插",
        }
    )

    # 最新资产快照（全账号账本口径：负债与应急金是账号级概念）
    trend_rows = AssetDAO.trend(user_id)
    latest = trend_rows[-1] if trend_rows else None
    assets_total = float(latest["assets"]) if latest else 0.0
    liabilities_total = float(latest["liabilities"]) if latest else 0.0

    debt_ratio = None
    debt_score = None
    if latest and (assets_total + liabilities_total) > 0:
        # 输出百分数（0.3 → 30.0），与 formula 的「≤30%」口径一致
        debt_ratio = round2(
            liabilities_total / (assets_total + liabilities_total) * 100
        )
        debt_score = _clamp_score((70 - debt_ratio) / (70 - 30) * 100)
    items.append(
        {
            "key": "debt",
            "label": "负债率",
            "value": debt_ratio,
            "unit": "",
            "score": debt_score,
            "weight": HEALTH_WEIGHTS["debt"],
            "available": debt_ratio is not None,
            "hint": None if debt_ratio is not None else "暂无资产快照，无法评估负债率",
            "formula": "负债率 = 最新资产快照的负债合计 ÷（资产合计 + 负债合计）；"
            "≤30% 满分、≥70% 零分，线性内插",
        }
    )

    emergency_months = None
    emergency_score = None
    if latest and avg_expense > 0:
        emergency_months = round2(assets_total / avg_expense)
        emergency_score = _clamp_score(emergency_months / 6 * 100)
    items.append(
        {
            "key": "emergency",
            "label": "应急金月数",
            "value": emergency_months,
            "unit": "个月",
            "score": emergency_score,
            "weight": HEALTH_WEIGHTS["emergency"],
            "available": emergency_months is not None,
            "hint": (
                None
                if emergency_months is not None
                else "暂无资产快照或近 6 个月无支出记录，无法评估应急金"
            ),
            "formula": "应急金月数 = 最新资产快照的资产合计 ÷ 月均支出"
            f"（{window_label}）；≥6 个月满分、0 个月零分，线性内插",
        }
    )

    # 总分：缺数据分项不计分，按剩余权重归一
    weight_sum = sum(
        it["weight"] for it in items if it["available"] and it["score"] is not None
    )
    score_sum = sum(
        (it["score"] or 0) * it["weight"]
        for it in items
        if it["available"] and it["score"] is not None
    )
    total_score = _clamp_score(score_sum / weight_sum) if weight_sum > 0 else None
    if total_score is None:
        grade = "暂无法评估"
    elif total_score >= 80:
        grade = "优秀"
    elif total_score >= 60:
        grade = "良好"
    elif total_score >= 40:
        grade = "一般"
    else:
        grade = "待改善"

    return {
        "window": {
            "start": months[0],
            "end": months[-1],
            "months": HEALTH_WINDOW_MONTHS,
        },
        "avg_income": avg_income,
        "avg_expense": avg_expense,
        "score": total_score,
        "grade": grade,
        "items": items,
    }
