"""月度预算业务逻辑（按当前飞牛账号隔离，T-7.1 起按账本维度分账）

ledger_id 为 None 时：读路径不按账本过滤、写路径落到默认账本——升级前全库
只有默认账本，两种语义等价，因此不传 ledger_id 的旧调用行为不变。
"""

from typing import Optional

from app.core.constants import ROLE_ADMIN
from app.core.errors import (
    ErrorCode,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.db.dao.budget_dao import BudgetDAO
from app.db.dao.category_dao import CategoryDAO
from app.db.dao.family_dao import FamilyDAO
from app.db.dao.stat_dao import StatDAO
from app.schemas.budget import BudgetUpsert
from app.services import audit_service, ledger_service
from app.utils.amount import normalize_amount, round2
from app.utils.period import valid_month, month_range


def _validated_budget_payload(payload) -> tuple[str, str, float]:
    """个人/家庭预算共用的入参校验，返回 (month, category, amount)"""
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
    return payload.month, category, amount


def upsert_budget(
    payload: BudgetUpsert, user_id: str, ledger_id: Optional[int] = None
) -> dict:
    """新增/修改预算：月份格式与分类存在性校验（总预算行分类允许为空）"""
    month, category, amount = _validated_budget_payload(payload)
    # 账本维度（T-7.1）：写路径显式校验账本存在
    ledger_id = ledger_service.resolve_write(ledger_id)
    result = BudgetDAO.upsert(user_id, month, category, amount, ledger_id)
    audit_service.record(
        user_id,
        "budget.upsert",
        "budget",
        result["id"],
        "设置 " + month + " " + (category or "总预算") + " 预算 " + str(amount) + " 元",
    )
    return result


def delete_budget(budget_id: int, user_id: str) -> None:
    """删除预算，不存在抛 NotFoundError"""
    if not BudgetDAO.delete(budget_id, user_id):
        raise NotFoundError("预算不存在", code=ErrorCode.BUDGET_NOT_FOUND)
    audit_service.record(
        user_id,
        "budget.delete",
        "budget",
        budget_id,
        "删除预算 #" + str(budget_id),
    )


def overview(user_id: str, month: str, ledger_id: Optional[int] = None) -> dict:
    """某月预算进度总览：逐条预算对比实际支出（总预算对比当月全部支出）"""
    if not valid_month(month):
        raise ValidationError(
            "无效的月份格式，应为 YYYY-MM", code=ErrorCode.BUDGET_INVALID
        )
    start, end = month_range(month)
    budgets = BudgetDAO.list_month(user_id, month, ledger_id)

    expenses: dict[str, float] = {}
    for row in StatDAO.category_pie(user_id, start=start, end=end, ledger_id=ledger_id):
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


# ---- 家庭预算（T-7.3）：金额由家庭管理员设定，进度按全体成员支出汇总 ----


def _require_family(requester_id: str) -> dict:
    """要求当前账号已加入家庭，返回成员行"""
    member = FamilyDAO.member_of(requester_id)
    if member is None:
        raise NotFoundError("你还没有加入任何家庭")
    return member


def _require_family_admin(requester_id: str) -> dict:
    """要求当前账号是家庭管理员，返回成员行"""
    member = _require_family(requester_id)
    if member["role"] != ROLE_ADMIN:
        raise PermissionDeniedError("该操作仅限家庭管理员")
    return member


def family_overview(user_id: str, month: str) -> dict:
    """某月家庭预算进度总览：支出按全体成员当月实际支出合并

    家庭预算不按账本维度（成员各有账本，家庭口径 = 各账本之和），
    总预算行对比成员合计的全部支出，分类预算行对比该分类的成员合计。
    返回结构与个人 overview 一致，前端可复用同一套进度组件。
    """
    member = _require_family(user_id)
    if not valid_month(month):
        raise ValidationError(
            "无效的月份格式，应为 YYYY-MM", code=ErrorCode.BUDGET_INVALID
        )
    family = FamilyDAO.get(member["family_id"])
    if family is None:
        # 孤儿成员行（成员行指向已解散的家庭）：按未加入家庭处理而非 500
        raise NotFoundError("你所在的家庭不存在或已解散，请退出后重新加入或创建")
    start, end = month_range(month)
    budgets = BudgetDAO.list_family(family["id"], month)

    member_ids = [m["user_id"] for m in FamilyDAO.list_members(family["id"])]
    expenses: dict[str, float] = {}
    for row in StatDAO.category_pie_for_users(member_ids, start=start, end=end):
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


def upsert_family_budget(payload: BudgetUpsert, requester_id: str) -> dict:
    """新增/修改家庭预算（仅家庭管理员）"""
    member = _require_family_admin(requester_id)
    month, category, amount = _validated_budget_payload(payload)
    result = BudgetDAO.upsert_family(member["family_id"], month, category, amount)
    audit_service.record(
        requester_id,
        "budget.family_upsert",
        "budget",
        result["id"],
        "设置家庭 "
        + month
        + " "
        + (category or "总预算")
        + " 预算 "
        + str(amount)
        + " 元",
    )
    return result


def delete_family_budget(budget_id: int, requester_id: str) -> None:
    """删除家庭预算（仅家庭管理员，且只能删本家庭的预算行）"""
    member = _require_family_admin(requester_id)
    if not BudgetDAO.delete_in_family(budget_id, member["family_id"]):
        raise NotFoundError("预算不存在", code=ErrorCode.BUDGET_NOT_FOUND)
    audit_service.record(
        requester_id,
        "budget.family_delete",
        "budget",
        budget_id,
        "删除家庭预算 #" + str(budget_id),
    )
