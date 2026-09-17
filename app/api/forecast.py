"""预算建议与现金流预测接口（T-6.4，只读；按当前飞牛账号隔离）

采纳建议走既有 PUT /api/budget；本路由不新增写操作。
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, request_db_session
from app.schemas.common import ApiResponse, ok
from app.schemas.forecast import BudgetSuggestions, CashFlowForecast
from app.services import forecast_service

router = APIRouter(
    prefix="/api/forecast",
    tags=["预测与建议"],
    dependencies=[Depends(request_db_session)],
)


@router.get(
    "",
    response_model=ApiResponse[CashFlowForecast],
    summary="现金流预测：未来 N 天余额曲线（当前账号）",
)
def cash_flow(
    user: CurrentUser,
    horizon: int = Query(90, ge=7, le=180, description="预测天数（7-180）"),
    exclude: Optional[List[str]] = Query(
        None, description="排除的固定项 key（expense:商户 / income:商户），排除后重算"
    ),
):
    return ok(forecast_service.cash_flow(user.user_id, horizon, exclude))


@router.get(
    "/budget-suggestions",
    response_model=ApiResponse[BudgetSuggestions],
    summary="预算建议：近 6 个月分类中位数（当前账号）",
)
def budget_suggestions(
    user: CurrentUser,
    month: Optional[str] = Query(None, description="目标月份 YYYY-MM，默认当月"),
):
    return ok(forecast_service.budget_suggestions(user.user_id, month))
