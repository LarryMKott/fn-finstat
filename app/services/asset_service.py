"""资产快照业务逻辑（按当前飞牛账号隔离）"""

from typing import Optional

from app.db.dao.asset_dao import AssetDAO
from app.schemas.asset import AssetSnapshotCreate, AssetSnapshotUpdate, valid_date
from app.utils.amount import normalize_amount

ASSET_TYPES = {"asset", "liability"}


def list_snapshots(
    user_id: str, start: Optional[str] = None, end: Optional[str] = None
) -> list[dict]:
    """快照列表（新的在前）"""
    return AssetDAO.list_snapshots(user_id, start=start, end=end)


def create_snapshot(payload: AssetSnapshotCreate, user_id: str) -> dict:
    """新增快照：日期格式校验 + 金额归一化"""
    if not valid_date(payload.snap_date):
        raise ValueError("无效的快照日期，应为 YYYY-MM-DD")
    data = payload.model_dump()
    data["amount"] = normalize_amount(data["amount"])
    asset_id = AssetDAO.create(data, user_id)
    created = AssetDAO.get_by_id(asset_id, user_id)
    if created is None:
        raise KeyError("资产快照创建失败")
    return created


def update_snapshot(asset_id: int, payload: AssetSnapshotUpdate, user_id: str) -> dict:
    """部分更新快照，不存在抛 KeyError（路由转 404）"""
    if AssetDAO.get_by_id(asset_id, user_id) is None:
        raise KeyError("资产快照不存在")
    fields = {
        k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None
    }
    if "snap_date" in fields and not valid_date(fields["snap_date"]):
        raise ValueError("无效的快照日期，应为 YYYY-MM-DD")
    if "amount" in fields:
        fields["amount"] = normalize_amount(fields["amount"])
    if not AssetDAO.update(asset_id, fields, user_id):
        raise KeyError("资产快照不存在")
    updated = AssetDAO.get_by_id(asset_id, user_id)
    if updated is None:
        raise KeyError("资产快照不存在")
    return updated


def delete_snapshot(asset_id: int, user_id: str) -> None:
    if not AssetDAO.delete(asset_id, user_id):
        raise KeyError("资产快照不存在")


def trend(user_id: str) -> list[dict]:
    """净资产趋势（按快照日期汇总），金额保留 2 位小数"""
    points = []
    for row in AssetDAO.trend(user_id):
        assets = round(float(row["assets"] or 0), 2)
        liabilities = round(float(row["liabilities"] or 0), 2)
        points.append(
            {
                "date": row["snap_date"],
                "assets": assets,
                "liabilities": liabilities,
                "net": round(assets - liabilities, 2),
            }
        )
    return points
