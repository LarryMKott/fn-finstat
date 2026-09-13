"""月度预算业务逻辑（按当前飞牛账号隔离）"""

from app.core.errors import ErrorCode, NotFoundError, ValidationError
from app.db.dao.budget_dao import BudgetDAO
from app.db.dao.category_dao import CategoryDAO
from app.db.dao.stat_dao import StatDAO
from app.schemas.budget import BudgetUpsert
from app.utils.amount import normalize_amount, round2
from app.utils.period import valid_month, month_range


def upsert_budget(payload: BudgetUpsert, user_id: str) -> dict:
    """新增/修改预算：月份格式与分类存在性校验（总预算行分类允许为空）"""
    if not valid_month(payload.month):
        raise ValidationError(
            "无效的月份格式，应为 YYYY-MM", code=ErrorCode.BUDGET_INVALID
        )
    category = (payload.category or "").strip()
    if category and not CategoryDAO.get_by_name(category):
        raise ValidationError(
            f"分类「{category}」不存在，请先在分类管理中创建",
            code=ErrorCode.BUDGET_INVALID,
        )
    amount = normalize_amount(payload.amount)
    if amount <= 0:
        raise ValidationError("预算金额必须大于 0", code=ErrorCode.BUDGET_INVALID)
    return BudgetDAO.upsert(user_id, payload.month, category, amount)


def delete_budget(budget_id: int, user_id: str) -> None:
    """删除预算，不存在抛 NotFoundError"""
    if not BudgetDAO.delete(budget_id, user_id):
        raise NotFoundError("预算不存在", code=ErrorCode.BUDGET_NOT_FOUND)


def overview(user_id: str, month: str) -> dict:
    """某月预算进度总览：逐条预算对比实际支出（总预算对比当月全部支出）"""
    if not valid_month(month):
        raise ValidationError(
            "无效的月份格式，应为 YYYY-MM", code=ErrorCode.BUDGET_INVALID
        )
    start, end = month_range(month)
    budgets = BudgetDAO.list_month(user_id, month)

    expenses: dict[str, float] = {}
    for row in StatDAO.category_pie(user_id, start=start, end=end):
        expenses[row["name"]] = round2(row["value"])
    total_expense = round2(sum(expenses.values()))

    items = []
    overall = None
    for b in budgets:
        expense = expenses.get(b["category"], 0.0) if b["category"] else total_expense
        items.append(
            {
                "id": b["id"],
                "category": b["category"],
                "budget": round2(b["amount"]),
                "expense": expense,
                "remaining": round(b["amount"] - expense, 2),
            }
        )
        if not b["category"]:
            overall = round2(b["amount"])

    total_budget = (
        overall
        if overall is not None
        else round2(sum(b["amount"] for b in budgets if b["category"]))
    )
    return {
        "month": month,
        "total_budget": total_budget,
        "total_expense": total_expense,
        "items": items,
    }
