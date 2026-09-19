"""家庭空间接口（T-7.2 家庭空间与合并视图）

权限约定：家庭是用户自发组建的协作空间，操作只影响本家庭，与飞牛应用管理
无关——因此路由层全部用 CurrentUser，家庭管理员校验在服务层收口（不进
core/permissions 的应用级管理面策略表，与「全局影响操作才进管理面」的约定
一致）。隐私边界：家庭页汇总对成员开放；成员流水仅在 allow_detail_view
开启时可读（服务层强制，默认关闭）。
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, request_db_session
from app.schemas.bill import BillOut
from app.schemas.common import ApiResponse, PageResult, ok
from app.schemas.family import (
    FamilyCreate,
    FamilyInfoOut,
    FamilyJoin,
    FamilyJoinResult,
    FamilySettingsOut,
    FamilySettingsUpdate,
    FamilySummaryOut,
    InviteCodeOut,
)
from app.schemas.budget import BudgetOverview, BudgetUpsert
from app.core.errors import ValidationError
from app.services import budget_service, family_service

router = APIRouter(
    prefix="/api/family",
    tags=["家庭空间"],
    dependencies=[Depends(request_db_session)],
)


@router.get(
    "", response_model=ApiResponse[Optional[FamilyInfoOut]], summary="我的家庭信息"
)
def my_family(user: CurrentUser):
    """未加入家庭时 data 为 null；invite_code 仅家庭管理员可见"""
    return ok(family_service.my_family(user.user_id))


@router.post(
    "", response_model=ApiResponse[FamilyInfoOut], status_code=201, summary="创建家庭"
)
def create_family(user: CurrentUser, payload: FamilyCreate):
    """创建人自动成为家庭管理员；已在家庭中时 409"""
    family_service.create_family(payload.name, user.user_id, user.user_name)
    return ok(family_service.my_family(user.user_id))


@router.post(
    "/join", response_model=ApiResponse[FamilyJoinResult], summary="凭邀请码加入家庭"
)
def join_family(user: CurrentUser, payload: FamilyJoin):
    """邀请码无效 404；已在家庭中 409"""
    return ok(family_service.join_family(payload.code, user.user_id, user.user_name))


@router.post("/leave", status_code=204, summary="退出家庭")
def leave_family(user: CurrentUser):
    """普通成员直接退出；管理员在还有成员时须先解散（最后一人退出自动解散）"""
    family_service.leave_family(user.user_id)


@router.delete("", status_code=204, summary="解散家庭（家庭管理员）")
def disband_family(user: CurrentUser):
    """成员行与家庭一并删除，各成员自己的账单数据不受影响"""
    family_service.disband_family(user.user_id)


@router.put(
    "/settings",
    response_model=ApiResponse[FamilySettingsOut],
    summary="更新家庭设置（家庭管理员）",
)
def update_settings(user: CurrentUser, payload: FamilySettingsUpdate):
    """当前仅明细可见性开关：开启后成员可互看流水（只读），默认关闭"""
    return ok(family_service.update_settings(user.user_id, payload))


@router.post(
    "/invite/regenerate",
    response_model=ApiResponse[InviteCodeOut],
    summary="重新生成邀请码（家庭管理员）",
)
def regenerate_invite(user: CurrentUser):
    """旧邀请码立即失效"""
    return ok(family_service.regenerate_invite_code(user.user_id))


@router.delete("/members/{user_id}", status_code=204, summary="移除成员（家庭管理员）")
def remove_member(user: CurrentUser, user_id: str):
    """被移除成员的数据不受影响，仅解除家庭关联"""
    family_service.remove_member(user.user_id, user_id)


@router.get(
    "/summary",
    response_model=ApiResponse[FamilySummaryOut],
    summary="家庭月度汇总（聚合视图）",
)
def family_summary(
    user: CurrentUser,
    month: str = Query(..., description="月份，如 2026-09"),
):
    """汇总 = 各成员之和（逐成员给出收支与条数，可逐项核对）"""
    return ok(family_service.summary(user.user_id, month))


@router.get(
    "/members/{user_id}/bills",
    response_model=ApiResponse[PageResult[BillOut]],
    summary="查看成员流水（只读，需家庭开启明细可见）",
)
def member_bills(
    user: CurrentUser,
    user_id: str,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    month: Optional[str] = Query(None, description="按月筛选，如 2026-09"),
):
    """开关关闭时一律 403（含管理员）——隐私边界由家庭设置决定"""
    data = family_service.member_bills(
        user.user_id, user_id, page=page, page_size=page_size, month=month
    )
    return ok(
        PageResult(
            total=data["total"], page=page, page_size=page_size, items=data["rows"]
        )
    )


# ---- 家庭预算（T-7.3）：金额家庭管理员设定，进度按全体成员支出汇总 ----


@router.get(
    "/budgets",
    response_model=ApiResponse[BudgetOverview],
    summary="某月家庭预算进度总览（全体成员）",
)
def family_budget_overview(
    user: CurrentUser,
    month: str = Query(..., description="月份，如 2026-09"),
):
    """返回结构与个人预算总览一致（items/total_budget/total_expense），
    支出口径为全体成员当月实际支出（各账本之和）"""
    return ok(budget_service.family_overview(user.user_id, month))


@router.put(
    "/budgets",
    response_model=ApiResponse[BudgetOverview],
    summary="新增/修改家庭预算（仅家庭管理员）",
)
def upsert_family_budget(user: CurrentUser, payload: BudgetUpsert):
    """按（家庭+月份+分类）upsert；预算不按账本维度，payload.ledger_id 不接受
    （家庭口径 = 成员各账本之和），误传时返回 400"""
    if payload.ledger_id is not None:
        raise ValidationError("家庭预算不按账本维度，请移除 ledger_id")
    budget_service.upsert_family_budget(payload, user.user_id)
    return ok(budget_service.family_overview(user.user_id, payload.month))


@router.delete(
    "/budgets/{budget_id}",
    status_code=204,
    summary="删除家庭预算（仅家庭管理员，限本家庭）",
)
def delete_family_budget(user: CurrentUser, budget_id: int):
    budget_service.delete_family_budget(budget_id, user.user_id)
