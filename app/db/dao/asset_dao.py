"""资产快照数据访问层（SQLAlchemy ORM，按 user_id 归属账号）"""

from typing import Optional

from sqlalchemy import case, delete, func, select, update

from app.db.base import get_db
from app.db.ledgers import resolve_ledger_id
from app.db.models import AssetSnapshot


class AssetDAO:
    @staticmethod
    def list_snapshots(
        user_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 1000,
        ledger_id: Optional[int] = None,
    ) -> list[dict]:
        """资产快照列表（当前账号），按日期倒序（新的在前）

        ledger_id 为 None 时不按账本过滤（旧调用行为不变）。
        """
        conds = [AssetSnapshot.user_id == user_id]
        if ledger_id is not None:
            conds.append(AssetSnapshot.ledger_id == ledger_id)
        if start:
            conds.append(AssetSnapshot.snap_date >= start)
        if end:
            conds.append(AssetSnapshot.snap_date <= end)
        with get_db() as session:
            stmt = (
                select(AssetSnapshot)
                .where(*conds)
                .order_by(AssetSnapshot.snap_date.desc(), AssetSnapshot.id.desc())
                .limit(limit)
            )
            return [a.as_dict() for a in session.scalars(stmt)]

    @staticmethod
    def get_by_id(asset_id: int, user_id: str) -> Optional[dict]:
        """按主键查快照（仅限当前账号），不存在返回 None"""
        with get_db() as session:
            snapshot = session.scalar(
                select(AssetSnapshot).where(
                    AssetSnapshot.id == asset_id, AssetSnapshot.user_id == user_id
                )
            )
            return snapshot.as_dict() if snapshot is not None else None

    @staticmethod
    def create(data: dict, user_id: str, ledger_id: Optional[int] = None) -> int:
        """新增快照；ledger_id 为 None 时落到默认账本（旧调用行为不变）"""
        values = dict(data)
        values.pop("ledger_id", None)  # 账本由下方统一解析，避免重复关键字
        with get_db() as session:
            snapshot = AssetSnapshot(
                **values,
                user_id=user_id,
                ledger_id=resolve_ledger_id(
                    session, ledger_id or data.get("ledger_id")
                ),
            )
            session.add(snapshot)
            session.flush()
            return snapshot.id

    @staticmethod
    def update(asset_id: int, fields: dict, user_id: str) -> bool:
        if not fields:
            return False
        with get_db() as session:
            rowcount = session.execute(
                update(AssetSnapshot)
                .where(AssetSnapshot.id == asset_id, AssetSnapshot.user_id == user_id)
                .values(**fields)
            ).rowcount
        return rowcount > 0

    @staticmethod
    def delete(asset_id: int, user_id: str) -> bool:
        with get_db() as session:
            rowcount = session.execute(
                delete(AssetSnapshot).where(
                    AssetSnapshot.id == asset_id, AssetSnapshot.user_id == user_id
                )
            ).rowcount
        return rowcount > 0

    @staticmethod
    def trend(user_id: str, ledger_id: Optional[int] = None) -> list[dict]:
        """按快照日期汇总：assets 资产合计 / liabilities 负债合计 / net 净资产，日期升序

        ledger_id 为 None 时不按账本过滤（旧调用行为不变）。
        """
        day = AssetSnapshot.snap_date
        assets = func.coalesce(
            func.sum(
                case(
                    (AssetSnapshot.asset_type == "asset", AssetSnapshot.amount),
                    else_=0.0,
                )
            ),
            0.0,
        ).label("assets")
        liabilities = func.coalesce(
            func.sum(
                case(
                    (AssetSnapshot.asset_type == "liability", AssetSnapshot.amount),
                    else_=0.0,
                )
            ),
            0.0,
        ).label("liabilities")
        conds = [AssetSnapshot.user_id == user_id]
        if ledger_id is not None:
            conds.append(AssetSnapshot.ledger_id == ledger_id)
        with get_db() as session:
            stmt = (
                select(day, assets, liabilities)
                .where(*conds)
                .group_by(day)
                .order_by(day)
            )
            return [dict(r) for r in session.execute(stmt).mappings()]
