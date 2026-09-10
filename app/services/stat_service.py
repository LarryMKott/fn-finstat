"""统计报表业务逻辑（仅统计当前飞牛账号的账单）"""
from typing import Optional

from app.db.dao.stat_dao import StatDAO


def _round2(value) -> float:
    return round(float(value or 0), 2)


def summary(
    user_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    account: Optional[str] = None,
    tx_type: Optional[str] = None,
) -> dict:
    data = StatDAO.summary(user_id, start, end, account, tx_type)
    income = _round2(data["income"])
    expense = _round2(data["expense"])
    return {"income": income, "expense": expense, "net": round(income - expense, 2)}


def month_trend(
    user_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    account: Optional[str] = None,
    tx_type: Optional[str] = None,
) -> list[dict]:
    rows = StatDAO.month_trend(user_id, start, end, account, tx_type)
    return [
        {
            "month": r["month"],
            "income": _round2(r["income"]),
            "expense": _round2(r["expense"]),
        }
        for r in rows
    ]


def category_pie(
    user_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    account: Optional[str] = None,
) -> list[dict]:
    rows = StatDAO.category_pie(user_id, start, end, account)
    return [{"name": r["name"], "value": _round2(r["value"])} for r in rows]


def merchant_top(
    user_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    account: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    rows = StatDAO.merchant_top(user_id, start, end, account, limit)
    return [
        {"merchant": r["merchant"], "amount": _round2(r["amount"]), "count": r["count"]}
        for r in rows
    ]
