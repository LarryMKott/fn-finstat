"""借贷台账接口（T-7.5，数据按当前飞牛账号隔离）

借出（应收）/ 借入（应付）的本金与还款跟踪：独立小台账，与流水不强制
关联；「还清即结项」由服务层按还款合计自动推导。
"""

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, request_db_session
from app.schemas.common import ApiResponse, ok
from app.schemas.loan import (
    LoanCreate,
    LoanLedger,
    LoanOut,
    LoanPaymentCreate,
    LoanPayments,
    LoanUpdate,
)
from app.services import loan_service

router = APIRouter(
    prefix="/api/loans",
    tags=["借贷台账"],
    dependencies=[Depends(request_db_session)],
)


@router.get(
    "",
    response_model=ApiResponse[LoanLedger],
    summary="借贷台账（含应收 / 应付汇总与逐条进度）",
)
def list_loans(user: CurrentUser):
    return ok(loan_service.list_loans(user.user_id))


@router.post(
    "",
    response_model=ApiResponse[LoanOut],
    status_code=201,
    summary="登记一笔借出 / 借入",
)
def create_loan(user: CurrentUser, payload: LoanCreate):
    return ok(loan_service.create_loan(payload, user.user_id))


@router.put(
    "/{loan_id}",
    response_model=ApiResponse[LoanOut],
    summary="更新借贷信息（对方 / 本金 / 日期 / 备注）",
)
def update_loan(user: CurrentUser, loan_id: int, payload: LoanUpdate):
    return ok(loan_service.update_loan(loan_id, payload, user.user_id))


@router.delete(
    "/{loan_id}",
    status_code=204,
    summary="删除借贷及其全部还款记录",
)
def delete_loan(user: CurrentUser, loan_id: int):
    loan_service.delete_loan(loan_id, user.user_id)


@router.get(
    "/{loan_id}/payments",
    response_model=ApiResponse[LoanPayments],
    summary="还款明细与进度",
)
def list_payments(user: CurrentUser, loan_id: int):
    return ok(loan_service.list_payments(loan_id, user.user_id))


@router.post(
    "/{loan_id}/payments",
    response_model=ApiResponse[LoanPayments],
    summary="登记一笔还款（合计达到本金自动结清）",
)
def add_payment(user: CurrentUser, loan_id: int, payload: LoanPaymentCreate):
    return ok(loan_service.add_payment(loan_id, payload, user.user_id))


@router.delete(
    "/{loan_id}/payments/{payment_id}",
    response_model=ApiResponse[LoanPayments],
    summary="删除一条还款记录（不足本金后回到进行中）",
)
def delete_payment(user: CurrentUser, loan_id: int, payment_id: int):
    return ok(loan_service.delete_payment(loan_id, payment_id, user.user_id))
