"""报销 / 垫付工作流接口（T-7.4，数据按当前飞牛账号隔离）

报销单跟踪一组支出的回收进度（待提交 → 已提交 → 部分到账 → 已结清）；
统计口径不变：报销支出仍计入支出，报销单只跟踪回收进度。
"""

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, request_db_session
from app.schemas.common import ApiResponse, ok
from app.schemas.reimb import (
    ReimbAttachResult,
    ReimbBillIdsRequest,
    ReimbBillList,
    ReimbursementCreate,
    ReimbursementOut,
    ReimbursementUpdate,
)
from app.services import reimb_service

router = APIRouter(
    prefix="/api/reimb",
    tags=["报销垫付"],
    dependencies=[Depends(request_db_session)],
)


@router.get(
    "",
    response_model=ApiResponse[list[ReimbursementOut]],
    summary="报销单列表（含流水笔数与金额合计）",
)
def list_claims(user: CurrentUser):
    return ok(reimb_service.list_claims(user.user_id))


@router.post(
    "",
    response_model=ApiResponse[ReimbursementOut],
    status_code=201,
    summary="新建报销单（待提交）",
)
def create_claim(user: CurrentUser, payload: ReimbursementCreate):
    return ok(reimb_service.create_claim(payload, user.user_id))


@router.put(
    "/{claim_id}",
    response_model=ApiResponse[ReimbursementOut],
    summary="更新报销单（名称 / 备注 / 状态流转 / 到账登记）",
)
def update_claim(user: CurrentUser, claim_id: int, payload: ReimbursementUpdate):
    return ok(reimb_service.update_claim(claim_id, payload, user.user_id))


@router.delete("/{claim_id}", status_code=204, summary="删除报销单（其下流水摘除）")
def delete_claim(user: CurrentUser, claim_id: int):
    reimb_service.delete_claim(claim_id, user.user_id)


@router.get(
    "/{claim_id}/bills",
    response_model=ApiResponse[ReimbBillList],
    summary="报销单内流水明细",
)
def list_claim_bills(user: CurrentUser, claim_id: int):
    return ok(reimb_service.list_claim_bills(claim_id, user.user_id))


@router.post(
    "/{claim_id}/bills",
    response_model=ApiResponse[ReimbAttachResult],
    summary="把勾选的支出流水加入报销单",
)
def attach_bills(
    user: CurrentUser,
    claim_id: int,
    payload: ReimbBillIdsRequest,
):
    """仅支出流水可加入；已在回收站或其他报销单的流水会被拒绝并提示"""
    return ok(reimb_service.attach_bills(claim_id, payload.ids, user.user_id))


@router.delete(
    "/{claim_id}/bills",
    response_model=ApiResponse[ReimbAttachResult],
    summary="从报销单摘除流水（报销标记复位）",
)
def detach_bills(
    user: CurrentUser,
    claim_id: int,
    payload: ReimbBillIdsRequest,
):
    return ok(reimb_service.detach_bills(claim_id, payload.ids, user.user_id))
