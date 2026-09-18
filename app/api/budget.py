"""月度预算接口（按当前飞牛账号隔离，T-7.1 起支持账本维度）

ledger_id 不传时为默认账本（写入）／不按账本过滤（读取），旧调用行为不变。
"""

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, request_db_session
from app.api.params import LedgerIdQuery
from app.schemas.budget import BudgetOverview, BudgetProgress, BudgetUpsert
from app.schemas.common import ApiResponse, ok
from app.services import budget_service

router = APIRouter(
    prefix="/api/budget",
    tags=["预算管理"],
    dependencies=[Depends(request_db_session)],
)


@router.get(
    "",
    response_model=ApiResponse[BudgetOverview],
    summary="某月预算进度总览（当前账号）",
)
def get_overview(
    user: CurrentUser,
    month: str = Query(..., description="月份，如 2026-09"),
    ledger_id: LedgerIdQuery = None,
):
    return ok(budget_service.overview(user.user_id, month, ledger_id))


@router.put(
    "",
    response_model=ApiResponse[BudgetProgress],
    summary="新增/修改预算（按月+分类 upsert）",
)
def upsert_budget(user: CurrentUser, payload: BudgetUpsert):
    """返回值附带该分类当月实际支出与剩余预算（重新计算总览后取对应条目）"""
    budget = budget_service.upsert_budget(payload, user.user_id, payload.ledger_id)
    data = budget_service.overview(user.user_id, payload.month, payload.ledger_id)
    for item in data["items"]:
        if item["category"] == budget["category"]:
            return ok(BudgetProgress(**item))
    return ok(
        BudgetProgress(
            id=budget["id"],
            category=budget["category"],
            budget=budget["amount"],
            expense=0.0,
            remaining=budget["amount"],
        )
    )


@router.delete("/{budget_id}", status_code=204, summary="删除预算（仅当前账号）")
def delete_budget(user: CurrentUser, budget_id: int):
    budget_service.delete_budget(budget_id, user.user_id)
