"""资产快照业务逻辑（按当前飞牛账号隔离）"""

from typing import Optional

from app.core.errors import ErrorCode, EnvironmentError_, NotFoundError, ValidationError
from app.db.dao.asset_dao import AssetDAO
from app.schemas.asset import AssetSnapshotCreate, AssetSnapshotUpdate, valid_date
from app.utils.amount import normalize_amount, round2

ASSET_TYPES = {"asset", "liability"}


def list_snapshots(
    user_id: str, start: Optional[str] = None, end: Optional[str] = None
) -> list[dict]:
    """快照列表（新的在前）"""
    return AssetDAO.list_snapshots(user_id, start=start, end=end)


def create_snapshot(payload: AssetSnapshotCreate, user_id: str) -> dict:
    """新增快照：日期格式校验 + 金额归一化"""
    if not valid_date(payload.snap_date):
        raise ValidationError(
            "无效的快照日期，应为 YYYY-MM-DD", code=ErrorCode.ASSET_INVALID
        )
    data = payload.model_dump()
    data["amount"] = normalize_amount(data["amount"])
    asset_id = AssetDAO.create(data, user_id)
    created = AssetDAO.get_by_id(asset_id, user_id)
    if created is None:
        raise EnvironmentError_("资产快照创建失败：写入后无法取回记录")
    return created


def update_snapshot(asset_id: int, payload: AssetSnapshotUpdate, user_id: str) -> dict:
    """部分更新快照，不存在抛 NotFoundError"""
    if AssetDAO.get_by_id(asset_id, user_id) is None:
        raise NotFoundError("资产快照不存在", code=ErrorCode.ASSET_NOT_FOUND)
    fields = {
        k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None
    }
    if "snap_date" in fields and not valid_date(fields["snap_date"]):
        raise ValidationError(
            "无效的快照日期，应为 YYYY-MM-DD", code=ErrorCode.ASSET_INVALID
        )
    if "amount" in fields:
        fields["amount"] = normalize_amount(fields["amount"])
    if not AssetDAO.update(asset_id, fields, user_id):
        raise NotFoundError("资产快照不存在", code=ErrorCode.ASSET_NOT_FOUND)
    updated = AssetDAO.get_by_id(asset_id, user_id)
    if updated is None:
        raise NotFoundError("资产快照不存在", code=ErrorCode.ASSET_NOT_FOUND)
    return updated


def delete_snapshot(asset_id: int, user_id: str) -> None:
    if not AssetDAO.delete(asset_id, user_id):
        raise NotFoundError("资产快照不存在", code=ErrorCode.ASSET_NOT_FOUND)


def trend(user_id: str) -> list[dict]:
    """净资产趋势（按快照日期汇总），金额保留 2 位小数"""
    points = []
    for row in AssetDAO.trend(user_id):
        assets = round2(row["assets"])
        liabilities = round2(row["liabilities"])
        points.append(
            {
                "date": row["snap_date"],
                "assets": assets,
                "liabilities": liabilities,
                "net": round(assets - liabilities, 2),
            }
        )
    return points
