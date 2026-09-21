"""异常自检与主动通知（AI-1，脑洞清单 §7.2 落定口径）

产品定位：把「我打开应用才查账」变成「有事主动告诉我」。全部判定为
**纯规则计算，零 AI 成本**，复用已交付的预测/固定项识别计算结果。

四类异常（阈值口径为 2026-09-20 用户决策，勿随意调整）：
- 分类支出抬高：当月某分类支出 > 近 6 个完整月同分类月中位数 × 1.5
  （趋势异常用低阈值捕捉苗头，与单笔离群的 3× 是两回事，互不替代）
- 一次性大额：单笔 > 该分类月度中位数 × forecast_service.OUTLIER_FACTOR(3)
- 越悲观线：当月累计支出 > 悲观月支出预估（可变支出 P90 + 固定支出月合计，
  复用 forecast_service.cash_flow 的输出）
- 固定项断供/突变：固定项商户本月缺失（过扣款日 3 天宽限后）或金额
  偏离月均带宽 forecast_service.FIXED_AMOUNT_BAND(±25%)

推送策略：**周级**（调度周期 7 天）+ 单周期同类异常合并为一条通知
（按类别分组），避免通知疲劳；事件类型 anomaly_alert 在通知中心
逐类可关。多账号逐账号独立检查，各自收到各自的合并通知。
"""

import logging
import statistics
from datetime import date

from sqlalchemy import select

from app.db.base import get_db
from app.db.dao.bill_dao import BillDAO
from app.db.dao.stat_dao import StatDAO
from app.db.models import Bill
from app.services import forecast_service, notify_service
from app.utils.period import last_full_months, month_range

logger = logging.getLogger(__name__)

TASK_KEY = "anomaly_weekly"
# 周级推送（决策 §7.2）：7 天 × 24 小时 × 60 分钟
TASK_INTERVAL_MINUTES = 7 * 24 * 60

ANOMALY_CATEGORY_FACTOR = 1.5  # 分类支出抬高的判定倍数（决策 §7.2）
# 分类中位数的最少样本月数：不足 3 个有数据的月份不判定（数据太少全是噪声）
_MIN_MONTHS_FOR_MEDIAN = 3
# 固定项「断供」判定：过常规扣款日 3 天仍无流水才报警（月初误报缓冲）
_MISS_GRACE_DAYS = 3
# 单次通知最多条数：异常再多也只报最刺眼的，细节去流水页看
_MAX_LINES = 8


def _distinct_user_ids() -> list[str]:
    with get_db() as session:
        rows = session.execute(
            select(Bill.user_id).where(Bill.deleted.is_(False)).distinct()
        ).all()
        return sorted(r[0] for r in rows if r[0])


def _category_median_map(user_id: str, months: list[str]) -> dict[str, float]:
    """近 N 个完整月逐分类的月度合计中位数（只有 ≥3 个非零月才参与判定）"""
    totals: dict[str, list[float]] = {}
    for m in months:
        start, end = month_range(m)
        for row in StatDAO.category_pie(user_id, start=start, end=end):
            name = row["name"] or "（未分类）"
            totals.setdefault(name, []).append(float(row["value"]))
    return {
        name: statistics.median(values)
        for name, values in totals.items()
        if len(values) >= _MIN_MONTHS_FOR_MEDIAN
    }


def _check_user(user_id: str, today: date) -> list[str]:
    """对单账号执行四类异常判定，返回异常描述行（无异常返回空表）"""
    months = last_full_months(today, 6)
    cur_month = f"{today.year:04d}-{today.month:02d}"
    cur_start, cur_end = month_range(cur_month)

    medians = _category_median_map(user_id, months)

    # 当月分类合计与流水（一次取回，四类判定共用）
    cur_pie = StatDAO.category_pie(user_id, start=cur_start, end=cur_end)
    cur_cat = {
        (row["name"] or "（未分类）"): float(row["value"])
        for row in cur_pie
        if float(row["value"]) > 0
    }
    cur_total = round(sum(cur_cat.values()), 2)
    _, cur_bills = BillDAO.list_bills(
        user_id, page=1, page_size=500, start=cur_start, end=cur_end
    )
    cur_expenses = [b for b in cur_bills if b["tx_type"] == "expense"]

    lines: list[str] = []

    # 1. 分类支出抬高（趋势异常，1.5×）
    for name, total in cur_cat.items():
        median = medians.get(name)
        if median and median > 0 and total > median * ANOMALY_CATEGORY_FACTOR:
            lines.append(
                f"分类「{name}」本月已支出 {total} 元，达近 6 个月"
                f"月中位数（{round(median, 2)} 元）的 {total / median:.1f} 倍"
            )

    # 2. 单笔一次性大额（离群，3×，复用预测的 OUTLIER_FACTOR）
    for b in cur_expenses:
        median = medians.get(b["category"] or "（未分类）")
        if (
            median
            and median > 0
            and float(b["amount"]) > median * forecast_service.OUTLIER_FACTOR
        ):
            lines.append(
                f"单笔大额：{b['merchant'] or '（无商户）'} {b['amount']} 元"
                f"（{b['category'] or '（未分类）'}），超过该分类月中位数 "
                f"{forecast_service.OUTLIER_FACTOR:.0f} 倍"
            )

    # 3/4. 悲观线与固定项突变：复用现金流预测一次算好的口径
    try:
        cf = forecast_service.cash_flow(user_id, horizon=7, today=today)
    except Exception:
        logger.warning(
            "账号 %s 异常自检：现金流预测失败，跳过悲观线与固定项检查", user_id
        )
        cf = None
    if cf is not None:
        variable = cf["variable"]
        fixed_expense = round(
            sum(
                i["monthly_amount"]
                for i in cf["fixed_items"]
                if i["tx_type"] == "expense"
            ),
            2,
        )
        pessimistic = round(variable["p90_monthly"] + fixed_expense, 2)
        if pessimistic > 0 and cur_total > pessimistic:
            lines.append(
                f"本月累计支出 {cur_total} 元，已超过预测悲观月支出线"
                f"（{pessimistic} 元）"
            )

        merchant_cur: dict[str, float] = {}
        for b in cur_expenses:
            if b["merchant"]:
                merchant_cur[b["merchant"]] = merchant_cur.get(
                    b["merchant"], 0.0
                ) + float(b["amount"])
        for item in cf["fixed_items"]:
            if item["tx_type"] != "expense":
                continue
            actual = round(merchant_cur.get(item["merchant"], 0.0), 2)
            expected = round(item["monthly_amount"], 2)
            if expected <= 0:
                continue
            if actual == 0:
                if today.day > min(item["day_of_month"], 28) + _MISS_GRACE_DAYS:
                    lines.append(
                        f"固定项「{item['merchant']}」本月未见扣款"
                        f"（月均 {expected} 元，疑似断供）"
                    )
            else:
                lo, hi = (
                    expected / forecast_service.FIXED_AMOUNT_BAND,
                    expected * forecast_service.FIXED_AMOUNT_BAND,
                )
                if not (lo <= actual <= hi):
                    lines.append(
                        f"固定项「{item['merchant']}」本月 {actual} 元，"
                        f"偏离月均 {expected} 元的 ±25% 带宽"
                    )

    return lines[:_MAX_LINES]


def weekly_check(today: date | None = None) -> tuple[int, str]:
    """周期任务入口（scheduler 契约）：全账号检查并合并推送，返回 (异常数, 摘要)"""
    today = today or date.today()
    users = _distinct_user_ids()
    total = 0
    notified = 0
    for user_id in users:
        try:
            lines = _check_user(user_id, today)
        except Exception:
            logger.exception("账号 %s 异常自检失败（不影响其他账号）", user_id)
            continue
        if not lines:
            continue
        total += len(lines)
        notify_service.notify_anomaly(user_id, lines, today=today)
        notified += 1
    return total, f"检查 {len(users)} 个账号，发现异常 {total} 条，推送 {notified} 人"
