"""月度预算接口（按当前飞牛账号隔离）"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import GatewayUser, get_gateway_user
from app.schemas.budget import BudgetOverview, BudgetProgress, BudgetUpsert
from app.services import budget_service

router = APIRouter(prefix="/api/budget", tags=["预算管理"])


@router.get("", response_model=BudgetOverview, summary="某月预算进度总览（当前账号）")
def get_overview(
    month: str = Query(..., description="月份，如 2026-09"),
    user: GatewayUser = Depends(get_gateway_user),
):
    try:
        return budget_service.overview(user.user_id, month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put(
    "", response_model=BudgetProgress, summary="新增/修改预算（按月+分类 upsert）"
)
def upsert_budget(payload: BudgetUpsert, user: GatewayUser = Depends(get_gateway_user)):
    """返回值附带该分类当月实际支出与剩余预算（重新计算总览后取对应条目）"""
    try:
        budget = budget_service.upsert_budget(payload, user.user_id)
        data = budget_service.overview(user.user_id, payload.month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for item in data["items"]:
        if item["category"] == budget["category"]:
            return BudgetProgress(**item)
    return BudgetProgress(
        id=budget["id"],
        category=budget["category"],
        budget=budget["amount"],
        expense=0.0,
        remaining=budget["amount"],
    )


@router.delete("/{budget_id}", status_code=204, summary="删除预算（仅当前账号）")
def delete_budget(budget_id: int, user: GatewayUser = Depends(get_gateway_user)):
    try:
        budget_service.delete_budget(budget_id, user.user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
