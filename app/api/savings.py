"""储蓄目标接口（T-1.4，数据按当前飞牛账号隔离）

进度 = 目标起始日以来的累计净结余（收入 − 支出），由流水实时计算；
与流水不按账本维度（全部账本合并口径），回收站流水自动排除。
"""

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, request_db_session
from app.schemas.common import ApiResponse, ok
from app.schemas.savings import (
    SavingsGoalCreate,
    SavingsGoalList,
    SavingsGoalOut,
    SavingsGoalUpdate,
)
from app.services import savings_service

router = APIRouter(
    prefix="/api/savings-goals",
    tags=["储蓄目标"],
    dependencies=[Depends(request_db_session)],
)


@router.get(
    "",
    response_model=ApiResponse[SavingsGoalList],
    summary="储蓄目标列表（含结余自动计入的进度）",
)
def list_goals(user: CurrentUser):
    return ok(savings_service.list_goals(user.user_id))


@router.post(
    "",
    response_model=ApiResponse[SavingsGoalOut],
    status_code=201,
    summary="新建储蓄目标（起始日 = 创建日）",
)
def create_goal(user: CurrentUser, payload: SavingsGoalCreate):
    return ok(savings_service.create_goal(payload, user.user_id))


@router.put(
    "/{goal_id}",
    response_model=ApiResponse[SavingsGoalOut],
    summary="更新储蓄目标（名称 / 金额 / 目标日期 / 备注）",
)
def update_goal(user: CurrentUser, goal_id: int, payload: SavingsGoalUpdate):
    return ok(savings_service.update_goal(goal_id, payload, user.user_id))


@router.delete("/{goal_id}", status_code=204, summary="删除储蓄目标（不影响流水）")
def delete_goal(user: CurrentUser, goal_id: int):
    savings_service.delete_goal(goal_id, user.user_id)
