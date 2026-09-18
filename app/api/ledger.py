"""账本接口（T-7.1 账本维度）

权限约定与分类一致：账本是全局共享维度，新建/改名/删除会影响所有账号的
数据归属，写操作限管理员（独立部署/本地运行时守卫自动放行）。读操作公开，
便于前端在筛选器里列出可选账本。
"""

from fastapi import APIRouter, Depends

from app.api.deps import AdminUser, CurrentUser, request_db_session
from app.schemas.common import ApiResponse, ok
from app.schemas.ledger import (
    LedgerCreate,
    LedgerDeleteResult,
    LedgerOut,
    LedgerUpdate,
)
from app.services import ledger_service

router = APIRouter(
    prefix="/api/ledgers",
    tags=["账本管理"],
    dependencies=[Depends(request_db_session)],
)


@router.get("", response_model=ApiResponse[list[LedgerOut]], summary="获取账本列表")
def list_ledgers(user: CurrentUser):
    """默认账本排在最前；bill_count 为该账本下未删除的流水条数"""
    return ok(ledger_service.list_ledgers())


@router.post(
    "", response_model=ApiResponse[LedgerOut], status_code=201, summary="新建账本"
)
def create_ledger(user: AdminUser, payload: LedgerCreate):
    return ok(ledger_service.create_ledger(payload))


@router.put(
    "/{ledger_id}", response_model=ApiResponse[LedgerOut], summary="改名 / 改备注"
)
def update_ledger(user: AdminUser, ledger_id: int, payload: LedgerUpdate):
    return ok(ledger_service.update_ledger(ledger_id, payload))


@router.delete(
    "/{ledger_id}",
    response_model=ApiResponse[LedgerDeleteResult],
    summary="删除账本（数据并入默认账本）",
)
def delete_ledger(user: AdminUser, ledger_id: int):
    """默认账本不可删除；被删账本下的流水 / 预算 / 资产快照并入默认账本"""
    return ok(ledger_service.delete_ledger(ledger_id))
