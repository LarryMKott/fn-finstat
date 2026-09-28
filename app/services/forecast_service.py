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
from collections import Counter, defaultdict
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
# 支出结构拆分（T-1.5）窗口：比现金流窗口长，固定/弹性判定更稳
STRUCTURE_WINDOW_MONTHS = 6
# 日均折算约定（30 天/月）
DAYS_PER_MONTH = 30
# 排除 key 上限：防超长 query 撑爆 URL / 计算
MAX_EXCLUDE_KEYS = 50

# ---- 订阅侦探（AI-5）----
# 观察窗口（完整自然月）：需容纳「连续扣费满一年」的僵尸订阅判定
SUBSCRIPTION_WINDOW_MONTHS = 12
# 窗口内出现 ≥ 3 个月才按订阅分析（再少更像一次性消费）
SUBSCRIPTION_MIN_MONTHS = 3
# 台阶式涨价：新水平月费 ≥ 原水平 × 1.2，且此后每月不再回落到原水平
SUBSCRIPTION_PRICE_STEP_FACTOR = 1.2


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


def expense_structure(
    user_id: str,
    today: date | None = None,
    ledger_id: int | None = None,
) -> dict:
    """固定支出 vs 弹性支出拆分（T-1.5，只读，全部为真实账单统计）

    口径（随响应结构下发到界面）：
    - 窗口 = 近 6 个完整自然月；必选项（固定项）复用现金流预测的识别规则：
      同商户在窗口内每个完整月都出现、月度合计波动 ≤ 25%
    - 可砍项（弹性）= 其余支出，按商户聚合月均金额降序
    - 与预算建议同源：ledger_id 缺省不按账本过滤
    """
    today = today or date.today()
    months = last_full_months(today, STRUCTURE_WINDOW_MONTHS)
    start, _ = month_range(months[0])
    _, end = month_range(months[-1])
    rows = _parse_rows(
        StatDAO.forecast_rows(
            user_id, start=start, end=end, tx_type="expense", ledger_id=ledger_id
        )
    )

    fixed_items = _identify_fixed_items(rows, months)
    fixed_keys = {f["key"] for f in fixed_items}

    fixed_list = [
        {
            "merchant": f["merchant"],
            "monthly_amount": f["monthly_amount"],
            "monthly_totals": f["monthly_totals"],
        }
        for f in sorted(fixed_items, key=lambda f: -f["monthly_amount"])
    ]
    fixed_monthly = round2(sum(f["monthly_amount"] for f in fixed_list))

    flex_agg: dict[str, dict] = {}
    for row in rows:
        merchant = row["merchant"] or "（未填商户）"
        if f"expense:{row['merchant']}" in fixed_keys:
            continue
        agg = flex_agg.setdefault(merchant, {"total": 0.0, "count": 0, "months": set()})
        agg["total"] += row["amount"]
        agg["count"] += 1
        agg["months"].add(row["month"])

    month_count = len(months)
    flexible_list = [
        {
            "merchant": merchant,
            "monthly_amount": round2(agg["total"] / month_count),
            "total": round2(agg["total"]),
            "count": agg["count"],
            "months_hit": len(agg["months"]),
        }
        for merchant, agg in flex_agg.items()
    ]
    flexible_list.sort(key=lambda item: -item["monthly_amount"])
    flexible_monthly = round2(sum(f["monthly_amount"] for f in flexible_list))

    total_monthly = round2(fixed_monthly + flexible_monthly)
    return {
        "window": {"start": months[0], "end": months[-1], "months": month_count},
        "total_monthly": total_monthly,
        "fixed_monthly": fixed_monthly,
        "flexible_monthly": flexible_monthly,
        "fixed_pct": (
            round2(fixed_monthly / total_monthly * 100) if total_monthly > 0 else None
        ),
        "band_pct": round2((FIXED_AMOUNT_BAND - 1) * 100),
        "fixed": fixed_list,
        "flexible": flexible_list,
    }


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


# ---- 订阅侦探（AI-5）----


def _detect_price_step(active: list[tuple[str, float]]) -> dict | None:
    """台阶式涨价检测：某月起月费跳上台阶且此后每月都不再回落

    active 为按月升序的 (月份, 月度合计) 非零序列。判定：存在切分点 i 使
    后段**每个月**合计 ≥ 前段中位数 × SUBSCRIPTION_PRICE_STEP_FACTOR——
    「每月都不回落」保证台阶被保持，单月尖峰（年费、退款冲正）不会误报；
    to 取后段中位数（后段仍缓涨时给出代表性新水平），命中返回最早的切分点。
    """
    for i in range(1, len(active) - 1):
        prev_level = statistics.median([v for _, v in active[:i]])
        if prev_level <= 0:
            continue
        suffix = [v for _, v in active[i:]]
        if min(suffix) >= prev_level * SUBSCRIPTION_PRICE_STEP_FACTOR:
            new_level = statistics.median(suffix)
            return {
                "since": active[i][0],
                "from": round2(prev_level),
                "to": round2(new_level),
                "pct": round2((new_level / prev_level - 1) * 100),
            }
    return None


def subscriptions(
    user_id: str,
    today: date | None = None,
    ledger_id: int | None = None,
) -> dict:
    """订阅侦探（AI-5，只读）：把固定项识别换到「订阅视角」做聚合与体检

    与固定项识别（每月都出现 + 带宽 ≤25%）刻意不同：涨价月天然破坏带宽、
    断缴一个月的真实订阅也不该整项消失，故放宽为「窗口内出现 ≥ 3 个月」，
    再按时间线（连续扣费月数/首次出现）/ 台阶涨价 / 疑似僵尸三个维度单独
    判定。全部为真实账单统计，零 AI 成本；ledger_id 口径与预算建议一致。
    """
    today = today or date.today()
    months = last_full_months(today, SUBSCRIPTION_WINDOW_MONTHS)
    start, _ = month_range(months[0])
    _, end = month_range(months[-1])
    rows = _parse_rows(
        StatDAO.forecast_rows(user_id, start=start, end=end, ledger_id=ledger_id)
    )

    by_merchant: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["tx_type"] == TX_TYPE_EXPENSE and row["merchant"]:
            by_merchant[row["merchant"]].append(row)

    subs = []
    for merchant, bills in by_merchant.items():
        totals = {
            m: round2(sum(b["amount"] for b in bills if b["month"] == m))
            for m in months
        }
        active = [(m, v) for m, v in totals.items() if v > 0]
        if len(active) < SUBSCRIPTION_MIN_MONTHS:
            continue
        values = [v for _, v in active]
        streak = 0
        for m in reversed(months):
            if totals[m] > 0:
                streak += 1
            else:
                break
        price_step = _detect_price_step(active)
        cats = Counter(b["category"] for b in bills if b["category"])
        subs.append(
            {
                "merchant": merchant,
                "category": cats.most_common(1)[0][0] if cats else None,
                "monthly_amount": round2(statistics.median(values)),
                "last_amount": active[-1][1],
                "months_hit": len(active),
                "streak_months": streak,
                "first_month": active[0][0],
                "day_of_month": _fixed_amount_day(bills),
                "monthly_totals": {m: totals[m] for m in months if totals[m] > 0},
                "price_step": price_step,
                # 僵尸订阅：整个窗口每月都扣费、金额波动 ≤25% 且没涨过价
                # （涨价已被单独标出；带宽判定同时挡住涨价项，双保险口径一致）
                "zombie": (
                    price_step is None
                    and len(active) == len(months)
                    and max(values) / min(values) <= FIXED_AMOUNT_BAND
                ),
            }
        )
    subs.sort(key=lambda s: -s["monthly_amount"])

    monthly_total = round2(sum(s["monthly_amount"] for s in subs))
    income_by_month: dict[str, float] = defaultdict(float)
    for row in rows:
        if row["tx_type"] == TX_TYPE_INCOME:
            income_by_month[row["month"]] += row["amount"]
    income_nonzero = [round2(v) for v in income_by_month.values() if v > 0]
    income_monthly = (
        round2(statistics.median(income_nonzero)) if income_nonzero else 0.0
    )

    return {
        "window": {"start": months[0], "end": months[-1], "months": len(months)},
        "monthly_total": monthly_total,
        "income_monthly": income_monthly,
        "income_pct": (
            round2(monthly_total / income_monthly * 100) if income_monthly > 0 else None
        ),
        "caliber": {
            "min_months": SUBSCRIPTION_MIN_MONTHS,
            "price_step_factor": SUBSCRIPTION_PRICE_STEP_FACTOR,
            "band_pct": round2((FIXED_AMOUNT_BAND - 1) * 100),
        },
        "subscriptions": subs,
        "flag_counts": {
            "price_step": sum(1 for s in subs if s["price_step"]),
            "zombie": sum(1 for s in subs if s["zombie"]),
        },
    }


# ---- What-if 反事实模拟（AI-9）----
# 观察窗口（完整自然月）：与支出结构同长，分类月均更稳
WHAT_IF_WINDOW_MONTHS = 6
# 单次情景最多调整的分类数
WHAT_IF_MAX_SCOPES = 10
# 模拟月数上限
WHAT_IF_MAX_MONTHS = 36
# 基线返回的分类数（滑杆只给月均最高的前 N 类，长尾不参与模拟）
WHAT_IF_BASELINE_TOP = 8
# 单分类目标月支出上限（与账单金额同量级的防御值）
WHAT_IF_AMOUNT_MAX = 1_000_000.0


def what_if(
    user_id: str,
    adjustments: list[dict] | None = None,
    months: int = 12,
    today: date | None = None,
    ledger_id: int | None = None,
) -> dict:
    """What-if 反事实模拟（AI-9，只读，零 AI 成本）

    「把餐饮砍到 800/月，年底能多存多少」——把若干分类的月支出调整到目标值，
    按月均口径线性外推，回答两件事：能多存多少、储蓄目标能不能按时达成
    （与 T-1.4 储蓄目标联动，让模拟服务于真实问题）。

    口径（随响应回传）：
    - 窗口 = 近 6 个完整自然月；分类基线月均 = 窗口合计 ÷ 月数（与支出
      结构的弹性项同口径）；月结余基线 = 窗口净结余 ÷ 月数；
    - 调整按分类生效（人对「砍掉多少」的直觉是分类级，商户级太细）；
      目标金额可为 0（彻底砍掉），也可高于基线（模拟加码）；
    - 纯线性外推：累计影响 = 月度差额 × 模拟月数，不重算逐日余额曲线
      （固定项/可变支出的日级模型见 cash_flow，两者口径不同不混用）；
    - 目标可行性 = 调整后月结余 vs 目标所需月均结余（savings_service 进度
      口径）；未设目标日或目标日已过时如实返回 on_track=None，不编结论。
    """
    today = today or date.today()
    try:
        months = int(months)
    except (TypeError, ValueError):
        months = 12
    months = max(1, min(months, WHAT_IF_MAX_MONTHS))

    window_months = last_full_months(today, WHAT_IF_WINDOW_MONTHS)
    start, _ = month_range(window_months[0])
    _, end = month_range(window_months[-1])
    rows = _parse_rows(
        StatDAO.forecast_rows(user_id, start=start, end=end, ledger_id=ledger_id)
    )
    month_count = len(window_months)

    cat_total: dict[str, float] = defaultdict(float)
    income_total = 0.0
    expense_total = 0.0
    for row in rows:
        if row["tx_type"] == TX_TYPE_EXPENSE:
            cat_total[row["category"] or "（未分类）"] += row["amount"]
            expense_total += row["amount"]
        elif row["tx_type"] == TX_TYPE_INCOME:
            income_total += row["amount"]
    categories = sorted(
        (
            {"category": cat, "monthly_amount": round2(total / month_count)}
            for cat, total in cat_total.items()
        ),
        key=lambda item: -item["monthly_amount"],
    )
    monthly_income = round2(income_total / month_count)
    monthly_expense = round2(expense_total / month_count)
    monthly_savings = round2((income_total - expense_total) / month_count)

    baseline = {
        "categories": categories[:WHAT_IF_BASELINE_TOP],
        "monthly_expense": monthly_expense,
        "monthly_income": monthly_income,
        "monthly_savings": monthly_savings,
    }
    known = {c["category"]: c["monthly_amount"] for c in categories}

    items = _normalize_what_if(adjustments, known)
    scenario = None
    if items:
        delta_monthly = round2(sum(i["delta_monthly"] for i in items))
        scenario = {
            "items": items,
            "delta_monthly": delta_monthly,
            "months": months,
            "cumulative_delta": round2(delta_monthly * months),
            "monthly_savings_after": round2(monthly_savings + delta_monthly),
        }

    return {
        "window": {
            "start": window_months[0],
            "end": window_months[-1],
            "months": month_count,
        },
        "baseline": baseline,
        "scenario": scenario,
        "goal": _what_if_goal(user_id, scenario, today),
        "caliber": {
            "window_months": WHAT_IF_WINDOW_MONTHS,
            "months": months,
            "max_scopes": WHAT_IF_MAX_SCOPES,
            "baseline_top": WHAT_IF_BASELINE_TOP,
            "linear": "月均口径线性外推，不重算逐日余额曲线",
        },
        "notes": _what_if_notes(scenario, rows),
    }


def _normalize_what_if(
    adjustments: list[dict] | None, known: dict[str, float]
) -> list[dict]:
    """调整清单归一化与校验：分类必须在基线内出现，目标金额钳到 [0, 上限]"""
    if not adjustments:
        return []
    if len(adjustments) > WHAT_IF_MAX_SCOPES:
        raise ValidationError(f"一次最多调整 {WHAT_IF_MAX_SCOPES} 个分类")
    items: list[dict] = []
    seen: set[str] = set()
    for raw in adjustments:
        if not isinstance(raw, dict):
            raise ValidationError("调整项格式不合法")
        category = str(raw.get("category") or "").strip()[:64]
        if not category:
            raise ValidationError("调整分类不能为空")
        if category in seen:
            raise ValidationError(f"分类「{category}」重复调整")
        seen.add(category)
        if category not in known:
            raise ValidationError(
                f"分类「{category}」在近 {WHAT_IF_WINDOW_MONTHS} 个完整月没有支出记录，无法模拟"
            )
        try:
            target = round2(float(raw.get("monthly_amount")))
        except (TypeError, ValueError):
            raise ValidationError(f"分类「{category}」的目标金额不合法")
        if target < 0 or target > WHAT_IF_AMOUNT_MAX:
            raise ValidationError(
                f"分类「{category}」的目标金额需在 0 ~ {WHAT_IF_AMOUNT_MAX:.0f} 之间"
            )
        baseline_monthly = known[category]
        items.append(
            {
                "category": category,
                "baseline_monthly": baseline_monthly,
                "target_monthly": target,
                "delta_monthly": round2(baseline_monthly - target),
            }
        )
    return items


def _what_if_goal(user_id: str, scenario: dict | None, today: date) -> dict | None:
    """联动储蓄目标（T-1.4）：取未达成且目标日最近的一个，评估调整后能否按时达成

    无目标、已全部达成、或目标无法给出「所需月均」口径（未设目标日 / 目标日
    已过）时如实返回 None 或 on_track=None——模拟只外推结余，不编造可行性结论。
    """
    from app.services import savings_service

    goals = savings_service.list_goals(user_id, today=today)["items"]
    open_goals = [g for g in goals if not g["done"]]
    if not open_goals:
        return None
    with_deadline = sorted(
        (g for g in open_goals if g.get("target_date")),
        key=lambda g: g["target_date"],
    )
    goal = with_deadline[0] if with_deadline else open_goals[0]
    needed = goal.get("per_month_needed")
    months_left = goal.get("months_left")

    result = {
        "name": goal["name"],
        "target_amount": goal["target_amount"],
        "target_date": goal.get("target_date"),
        "saved": goal["saved"],
        "remaining": goal["remaining"],
        "months_left": months_left,
        "per_month_needed": needed,
    }
    if scenario is None:
        result["on_track"] = None
        return result
    after = scenario["monthly_savings_after"]
    result["monthly_savings_after"] = after
    if needed is None or needed <= 0 or (months_left is not None and months_left <= 0):
        # 未设目标日 / 目标日已过：没有可靠的「所需月均」口径，不编结论
        result["on_track"] = None
        return result
    result["on_track"] = after >= needed
    if not result["on_track"]:
        result["shortfall"] = round2(needed - after)
    return result


def _what_if_notes(scenario: dict | None, rows: list[dict]) -> list[str]:
    notes = [
        "分类基线月均 = 近 6 个完整月合计 ÷ 月数；月结余基线 = 窗口净结余 ÷ 月数",
        "累计影响 = 月度差额 × 模拟月数，按月均口径线性外推，不重算逐日余额曲线",
        "差额为正表示省得更多、为负表示花得更多；模拟不改变任何真实数据",
    ]
    if not rows:
        notes.append("窗口内没有流水，基线全为 0，模拟结果不具参考性")
    elif scenario is None:
        notes.append("未提交调整项，仅返回基线（供滑杆初始化）")
    return notes
