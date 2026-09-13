"""月度预算接口（按当前飞牛账号隔离）"""

from fastapi import APIRouter, Depends, Query

from app.api.deps import GatewayUser, get_gateway_user
from app.schemas.budget import BudgetOverview, BudgetProgress, BudgetUpsert
from app.schemas.common import ApiResponse, ok
from app.services import budget_service

router = APIRouter(prefix="/api/budget", tags=["预算管理"])


@router.get(
    "",
    response_model=ApiResponse[BudgetOverview],
    summary="某月预算进度总览（当前账号）",
)
def get_overview(
    month: str = Query(..., description="月份，如 2026-09"),
    user: GatewayUser = Depends(get_gateway_user),
):
    return ok(budget_service.overview(user.user_id, month))


@router.put(
    "",
    response_model=ApiResponse[BudgetProgress],
    summary="新增/修改预算（按月+分类 upsert）",
)
def upsert_budget(payload: BudgetUpsert, user: GatewayUser = Depends(get_gateway_user)):
    """返回值附带该分类当月实际支出与剩余预算（重新计算总览后取对应条目）"""
    budget = budget_service.upsert_budget(payload, user.user_id)
    data = budget_service.overview(user.user_id, payload.month)
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
def delete_budget(budget_id: int, user: GatewayUser = Depends(get_gateway_user)):
    budget_service.delete_budget(budget_id, user.user_id)
