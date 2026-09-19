"""预算建议 + 现金流预测（T-6.4）：全部数字由后端按确定规则计算，模型不参与

口径完全公开（每条都随响应回传，前端原样展示）：

预算建议（budget_suggestions）
- 窗口：目标月之前最近 6 个完整自然月（目标月当月不进窗口）；
- 一次性大额剔除：单笔金额 > 该分类「月度支出中位数 × 3」视为一次性大额，
  从月度合计中剔除并逐条列出。计划原文写「> 3σ」，但小样本下 σ 会被
  离群值本身抬高（300/300/300/20000 时 mean+3σ≈30000，永不触发），
  改用中位数基准的 3 倍——同样拦截单次大额采购，且对样本量不敏感；
- 建议值：出现月份 ≥ 3 的分类才给建议；建议 = 非零月度合计的中位数，
  区间 = 中位数 × 0.9 ~ × 1.1（调整系数）；采纳走既有预算 upsert，可手动微调。

现金流预测（cash_flow）
- 窗口：今日之前最近 3 个完整自然月；不足 3 个完整月时不识别固定项并如实说明；
- 固定项：同商户（区分收支）在窗口内每个完整月都出现，且各月合计
  max/min ≤ 1.25（金额落在同一区间）→ 订阅 / 房租 / 工资类；
- 可变支出：窗口内全部支出扣除固定项商户后的月度合计；P50 取非零月
  合计中位数，P90 取观察期最差月份（悲观线按最差月外推，不虚构分位数）；
- 可变收入不外推（偶发收入摊进未来会虚增余额，宁可保守）；固定收入照常投影；
- 日均按 30 天/月折算（与月份长短无关，便于人工核算）；
- 起点余额：有资产快照时 = 最近快照净资产 ± 快照日之后的收支净额
  （否则快照后的消费会被双计）；无快照时 = 全部流水净额，来源如实标注；
- 每个固定项可逐项排除后重算：被排除项的流水回落进可变支出，不凭空消失。

安全：只读查询全部走 StatDAO/AssetDAO 的只读方法，user_id 由服务端强制注入；
exclude 只接受已识别固定项的 key，未知值忽略并记日志。
"""

import logging
import statistics
from collections import defaultdict
from datetime import date, timedelta

from app.core.constants import TX_TYPE_EXPENSE, TX_TYPE_INCOME
from app.core.errors import ErrorCode, ValidationError
from app.db.dao.asset_dao import AssetDAO
from app.db.dao.budget_dao import BudgetDAO
from app.db.dao.stat_dao import StatDAO
from app.utils.amount import round2
from app.utils.period import last_full_months, month_range, shift_month, valid_month

logger = logging.getLogger(__name__)

# 预算建议：观察窗口（完整自然月数）与出现月份门槛
SUGGEST_WINDOW_MONTHS = 6
SUGGEST_MIN_MONTHS = 3
# 一次性大额：单笔 > 分类月度中位数 × 该系数
OUTLIER_FACTOR = 3.0
# 建议区间调整系数（下限 / 上限）
SUGGEST_LOW_FACTOR = 0.9
SUGGEST_HIGH_FACTOR = 1.1
# 现金流：固定项观察窗口（完整自然月数）与金额带宽（各月合计 max/min）
FORECAST_WINDOW_MONTHS = 3
FIXED_AMOUNT_BAND = 1.25
# 日均折算约定（30 天/月）
DAYS_PER_MONTH = 30
# 排除 key 上限：防超长 query 撑爆 URL / 计算
MAX_EXCLUDE_KEYS = 50


def _month_days(year: int, mon: int) -> int:
    if mon == 12:
        return 31
    return (date(year + 1, mon + 1, 1) - timedelta(days=1)).day


def _parse_rows(rows: list[dict]) -> list[dict]:
    """把 DAO 原始行收敛为 {day, month, tx_type, merchant, category, amount}

    只保留收支两类（转账是内部资金移动，不影响净额与支出结构）。
    """
    parsed = []
    for r in rows:
        tx_type = r["tx_type"]
        if tx_type not in (TX_TYPE_EXPENSE, TX_TYPE_INCOME):
            continue
        tx_time = str(r["tx_time"] or "")
        day = tx_time[:10]
        if len(day) != 10:
            continue
        parsed.append(
            {
                "day": day,
                "month": day[:7],
                "tx_type": tx_type,
                "merchant": (r["merchant"] or "").strip(),
                "category": (r["category"] or "").strip(),
                "amount": float(r["amount"] or 0),
            }
        )
    return parsed


# ---- 预算建议 ----


def budget_suggestions(
    user_id: str,
    month: str | None = None,
    today: date | None = None,
    ledger_id: int | None = None,
) -> dict:
    """目标月预算建议：近 6 个月分类支出中位数（剔除一次性大额）× 调整系数

    month 缺省为当月；建议只覆盖出现月份 ≥ 3 的分类，采纳由前端调
    既有 PUT /api/budget（本服务只读，不写预算）。
    ledger_id 为 T-7.1 账本口径（评审遗留收口）：None = 全部账本，
    此时 current_budget 合计全部账本的同月同分类预算；传账本时建议值与
    current_budget 同按该账本口径，两处口径严格一致。
    """
    today = today or date.today()
    month = month or f"{today.year:04d}-{today.month:02d}"
    if not valid_month(month):
        raise ValidationError(
            f"无效的月份格式，应为 YYYY-MM：{month}", code=ErrorCode.BUDGET_INVALID
        )

    window_months = [
        shift_month(month, -i) for i in range(SUGGEST_WINDOW_MONTHS, 0, -1)
    ]
    start, _ = month_range(window_months[0])
    _, end = month_range(window_months[-1])
    rows = _parse_rows(
        StatDAO.forecast_rows(
            user_id, start=start, end=end, tx_type="expense", ledger_id=ledger_id
        )
    )

    by_category: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_category[row["category"]].append(row)

    suggestions = []
    for category, bills in by_category.items():
        if not category:
            continue
        raw_totals = {
            m: round2(sum(b["amount"] for b in bills if b["month"] == m))
            for m in window_months
        }
        nonzero = [v for v in raw_totals.values() if v > 0]
        if len(nonzero) < SUGGEST_MIN_MONTHS:
            continue
        month_median = statistics.median(nonzero)
        threshold = month_median * OUTLIER_FACTOR
        outliers = [b for b in bills if b["amount"] > threshold]
        outlier_ids = {id(b) for b in outliers}
        kept_totals = {
            m: round2(
                sum(
                    b["amount"]
                    for b in bills
                    if b["month"] == m and id(b) not in outlier_ids
                )
            )
            for m in window_months
        }
        kept_nonzero = [v for v in kept_totals.values() if v > 0]
        if len(kept_nonzero) < SUGGEST_MIN_MONTHS:
            # 剔除后不足 3 个月说明该分类几乎全靠大额撑着，不建议
            continue
        suggested = round2(statistics.median(kept_nonzero))
        # current_budget 与建议值同口径（T-7.1 评审遗留收口）：不传账本 =
        # 全部账本的预算合计，传账本 = 仅该账本
        current = BudgetDAO.category_amount(user_id, month, category, ledger_id)
        suggestions.append(
            {
                "category": category,
                "suggested": suggested,
                "low": round2(suggested * SUGGEST_LOW_FACTOR),
                "high": round2(suggested * SUGGEST_HIGH_FACTOR),
                "months_used": len(kept_nonzero),
                "median": round2(month_median),
                "current_budget": current if current > 0 else None,
                "excluded_outliers": [
                    {
                        "tx_time": b["day"],
                        "merchant": b["merchant"],
                        "amount": round2(b["amount"]),
                    }
                    for b in outliers
                ],
            }
        )
    suggestions.sort(key=lambda s: s["suggested"], reverse=True)
    return {
        "month": month,
        "window": {"start": start, "end": end, "months": window_months},
        "suggestions": suggestions,
        "notes": [
            f"窗口为目标月前 {SUGGEST_WINDOW_MONTHS} 个完整月，出现 ≥ {SUGGEST_MIN_MONTHS} 个月的分类才给建议",
            f"单笔超过该分类月度中位数 {OUTLIER_FACTOR:.0f} 倍的支出视为一次性大额并剔除",
            "建议值为剔除后月度合计的中位数，区间为中位数 × 0.9 ~ × 1.1",
        ],
    }


# ---- 现金流预测 ----


def _fixed_amount_day(bills: list[dict]) -> int:
    """固定项扣款日：取该商户流水出现次数最多的日号（并列取较小日号）"""
    counter: dict[int, int] = defaultdict(int)
    for b in bills:
        counter[int(b["day"][8:10])] += 1
    return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _identify_fixed_items(rows: list[dict], window_months: list[str]) -> list[dict]:
    """固定项识别：同商户同收支类型在窗口内每个完整月都出现且月度合计同区间"""
    by_group: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row["merchant"]:
            by_group[(row["tx_type"], row["merchant"])].append(row)

    fixed = []
    for (tx_type, merchant), bills in sorted(by_group.items()):
        totals = {
            m: round2(sum(b["amount"] for b in bills if b["month"] == m))
            for m in window_months
        }
        if any(v <= 0 for v in totals.values()):
            continue  # 有月份未出现（合计 0）
        lo, hi = min(totals.values()), max(totals.values())
        if lo <= 0 or hi / lo > FIXED_AMOUNT_BAND:
            continue  # 金额波动超出同一区间
        monthly = round2(statistics.median(list(totals.values())))
        fixed.append(
            {
                "key": f"{tx_type}:{merchant}",
                "merchant": merchant,
                "tx_type": tx_type,
                "monthly_amount": monthly,
                "day_of_month": _fixed_amount_day(bills),
                "months_hit": len(window_months),
                "monthly_totals": {m: totals[m] for m in window_months},
                "_bills": bills,
            }
        )
    return fixed


def _start_balance(user_id: str, today: date) -> tuple[float, str, str | None]:
    """起点余额：优先资产快照（快照日之后的收支净额要补记），否则全量流水净额

    返回 (余额, 来源标识, 快照日期)；来源如实回传供前端展示口径。
    """
    trend = AssetDAO.trend(user_id)
    if trend:
        last = trend[-1]
        snap_net = round2(last["assets"]) - round2(last["liabilities"])
        snap_date = last["snap_date"]
        # 快照日之后的流水：next_day 起算，避免同日流水被双计（快照通常已含当日）
        next_day = (date.fromisoformat(snap_date) + timedelta(days=1)).isoformat()
        after = StatDAO.summary(user_id, start=next_day, end=today.isoformat())
        balance = snap_net + round2(after["income"]) - round2(after["expense"])
        return round2(balance), "asset_snapshot", snap_date
    total = StatDAO.summary(user_id, end=today.isoformat())
    return (
        round2(round2(total["income"]) - round2(total["expense"])),
        "bills_net",
        None,
    )


def cash_flow(
    user_id: str,
    horizon: int = 90,
    exclude: list[str] | None = None,
    today: date | None = None,
) -> dict:
    """未来 horizon 天余额曲线（P50 预期线 + P90 悲观线）

    exclude 为固定项 key 列表（"expense:商户" / "income:商户"），
    被排除项的流水回落进可变支出后重算——逐项排除即口径切换。
    """
    today = today or date.today()
    horizon = max(7, min(int(horizon), 180))
    exclude = [str(k)[:300] for k in (exclude or [])][:MAX_EXCLUDE_KEYS]

    window_months = last_full_months(today, FORECAST_WINDOW_MONTHS)
    start, _ = month_range(window_months[0])
    _, end = month_range(window_months[-1])
    rows = _parse_rows(StatDAO.forecast_rows(user_id, start=start, end=end))

    notes: list[str] = []
    fixed_all = _identify_fixed_items(rows, window_months)
    valid_keys = {item["key"] for item in fixed_all}
    unknown = [k for k in exclude if k not in valid_keys]
    if unknown:
        logger.info("现金流预测排除项不在固定项列表，已忽略：%s", unknown)
    exclude_set = {k for k in exclude if k in valid_keys}

    included = [i for i in fixed_all if i["key"] not in exclude_set]
    excluded = [i for i in fixed_all if i["key"] in exclude_set]
    included_merchants = {(i["tx_type"], i["merchant"]) for i in included}

    # 可变支出月度合计：窗口内支出流水扣除「已计入固定项」的商户
    # （被排除的固定项商户流水回落进可变，不凭空消失）
    variable_monthly: dict[str, float] = defaultdict(float)
    for row in rows:
        if row["tx_type"] != "expense":
            continue
        if (row["tx_type"], row["merchant"]) in included_merchants and row["merchant"]:
            continue
        variable_monthly[row["month"]] += row["amount"]
    observed = [
        {"month": m, "total": round2(variable_monthly.get(m, 0.0))}
        for m in window_months
    ]
    totals = [v["total"] for v in observed]
    nonzero = [v for v in totals if v > 0]
    # P50 用非零月中位数：尚未记账的月份合计为 0，混进中位数会系统性低估
    p50_monthly = round2(statistics.median(nonzero)) if nonzero else 0.0
    p90_monthly = round2(max(totals)) if totals else 0.0

    start_balance, start_source, snap_date = _start_balance(user_id, today)
    if start_source == "asset_snapshot":
        notes.append(f"起点余额取自 {snap_date} 的资产快照（并已补记快照日之后的收支）")
    else:
        notes.append("无资产快照，起点余额按全部流水净额（收入 − 支出）计算")

    points = []
    p50_cum = p90_cum = 0.0
    for i in range(1, horizon + 1):
        day = today + timedelta(days=i)
        days_in_month = _month_days(day.year, day.month)
        for item in included:
            if min(item["day_of_month"], days_in_month) != day.day:
                continue
            signed = item["monthly_amount"] * (1 if item["tx_type"] == "income" else -1)
            p50_cum += signed
            p90_cum += signed
        p50_cum -= p50_monthly / DAYS_PER_MONTH
        p90_cum -= p90_monthly / DAYS_PER_MONTH
        points.append(
            {
                "date": day.isoformat(),
                "p50": round2(start_balance + p50_cum),
                "p90": round2(start_balance + p90_cum),
            }
        )

    if not rows:
        notes.append("历史不足或没有流水，未识别固定项，曲线仅为起点余额直线")
    else:
        # 窗口固定取 3 个完整月，「历史不足」看数据是否覆盖整个窗口
        if min(r["month"] for r in rows) > window_months[0]:
            notes.append(
                f"完整月历史不足 {FORECAST_WINDOW_MONTHS} 个月，固定项识别可能偏保守"
            )
        notes.extend(
            [
                f"固定项 = 近 {len(window_months)} 个完整月每月出现且月度合计波动 ≤ "
                f"{(FIXED_AMOUNT_BAND - 1) * 100:.0f}% 的同商户收支",
                "可变支出的 P50 取非零月合计中位数、P90 取观察期最差月份，日均按 30 天/月折算",
                "可变收入不外推（偶发收入不摊进未来），仅固定收入按扣款日投影",
            ]
        )

    def _public_item(i: dict) -> dict:
        return {k: v for k, v in i.items() if not k.startswith("_")}

    return {
        "today": today.isoformat(),
        "horizon_days": horizon,
        "window": {"start": start, "end": end, "months": window_months},
        "start_balance": start_balance,
        "start_source": start_source,
        "snapshot_date": snap_date,
        "fixed_items": [_public_item(i) for i in included],
        "excluded_items": [_public_item(i) for i in excluded],
        "variable": {
            "p50_monthly": p50_monthly,
            "p90_monthly": p90_monthly,
            "monthly_totals": observed,
            "days_per_month": DAYS_PER_MONTH,
        },
        "points": points,
        "notes": notes,
    }
