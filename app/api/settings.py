"""应用设置接口"""
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import GatewayUser, get_gateway_user
from app.schemas.settings import (
    ConnectionTestResult, DatabaseInfo, MigrateResult, TargetDatabase, UserClaimResult,
)
from app.services import settings_service

router = APIRouter(prefix="/api/settings", tags=["应用设置"])


@router.get("/database", response_model=DatabaseInfo, summary="当前数据库信息（含当前账号）")
def get_database_info(user: GatewayUser = Depends(get_gateway_user)):
    return settings_service.get_database_info(user)


@router.post("/user/claim", response_model=UserClaimResult, summary="认领历史数据（归入当前账号）")
def claim_legacy_bills(user: GatewayUser = Depends(get_gateway_user)):
    """把升级前入库、无归属的历史流水认领到当前飞牛账号（本地模式无网关身份时无需认领）"""
    return settings_service.claim_legacy_bills(user)


@router.post("/database/test", response_model=ConnectionTestResult, summary="测试目标数据库连接")
def test_target_database(target: TargetDatabase):
    try:
        return settings_service.test_target_connection(target)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/database/migrate", response_model=MigrateResult, summary="把现有数据迁移到新数据库并切换")
def migrate_database(target: TargetDatabase):
    """搬移现有流水/分类到目标库并立即切换（源数据库保留不动，可回退）"""
    try:
        return settings_service.migrate_and_switch(target)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
