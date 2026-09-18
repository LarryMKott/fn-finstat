"""资产快照接口（净资产追踪，按当前飞牛账号隔离，T-7.1 起支持账本维度）"""

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, request_db_session
from app.api.params import EndQuery, LedgerIdQuery, StartQuery
from app.schemas.asset import (
    AssetSnapshotCreate,
    AssetSnapshotOut,
    AssetSnapshotUpdate,
    AssetTrendPoint,
)
from app.schemas.common import ApiResponse, ok
from app.services import asset_service

router = APIRouter(
    prefix="/api/asset",
    tags=["资产管理"],
    dependencies=[Depends(request_db_session)],
)


@router.get(
    "/trend",
    response_model=ApiResponse[list[AssetTrendPoint]],
    summary="净资产趋势（按快照日期汇总）",
)
def asset_trend(
    user: CurrentUser,
    ledger_id: LedgerIdQuery = None,
):
    return ok(asset_service.trend(user.user_id, ledger_id))


@router.get(
    "",
    response_model=ApiResponse[list[AssetSnapshotOut]],
    summary="资产快照列表（新的在前）",
)
def list_snapshots(
    user: CurrentUser,
    start: StartQuery = None,
    end: EndQuery = None,
    ledger_id: LedgerIdQuery = None,
):
    return ok(
        asset_service.list_snapshots(
            user.user_id, start=start, end=end, ledger_id=ledger_id
        )
    )


@router.post(
    "",
    response_model=ApiResponse[AssetSnapshotOut],
    status_code=201,
    summary="新增资产快照（归属当前账号）",
)
def create_snapshot(user: CurrentUser, payload: AssetSnapshotCreate):
    return ok(asset_service.create_snapshot(payload, user.user_id))


@router.put(
    "/{asset_id}",
    response_model=ApiResponse[AssetSnapshotOut],
    summary="编辑资产快照",
)
def update_snapshot(user: CurrentUser, asset_id: int, payload: AssetSnapshotUpdate):
    return ok(asset_service.update_snapshot(asset_id, payload, user.user_id))


@router.delete("/{asset_id}", status_code=204, summary="删除资产快照")
def delete_snapshot(user: CurrentUser, asset_id: int):
    asset_service.delete_snapshot(asset_id, user.user_id)
