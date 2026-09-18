"""账本数据访问层（T-7.1 账本维度）

账本是流水 / 预算 / 资产快照的归属维度。数据操作要点：
- 唯一约束为账本名（ledgers.name），重名由 translate_unique_violation 转业务异常
- 默认账本（is_default=True）不可删除：删除其他账本时其数据并入默认账本
- 搬迁预算时若目标账本已有同键预算（user_id + month + category），保留目标、
  丢弃被删账本的预算——预算是「设置」而非「事实数据」，不允许合并累加
"""

import time
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.base import get_db, translate_unique_violation
from app.db.ledgers import ensure_default_ledger, resolve_ledger_id
from app.db.models import AssetSnapshot, Bill, Budget, Ledger

__all__ = ["LedgerDAO", "ensure_default_ledger", "resolve_ledger_id"]


def _update_ledger(model, from_id: int, to_id: int):
    """生成把某表下某账本的数据整体改挂到目标账本的 UPDATE 语句"""
    return (
        update(model)
        .where(model.ledger_id == from_id)
        .values(ledger_id=to_id)
        .execution_options(synchronize_session=False)
    )


class LedgerDAO:
    @staticmethod
    def list_with_counts() -> list[dict]:
        """全部账本（默认账本在前、其余按 id 升序），附带未删除流水条数"""
        bill_count = func.count(Bill.id)
        with get_db() as session:
            rows = session.execute(
                select(Ledger, bill_count)
                .outerjoin(
                    Bill,
                    (Bill.ledger_id == Ledger.id) & (Bill.deleted.is_(False)),
                )
                .group_by(Ledger.id)
                .order_by(Ledger.is_default.desc(), Ledger.id)
            )
            return [
                {**ledger.as_dict(), "bill_count": int(count)} for ledger, count in rows
            ]

    @staticmethod
    def get(ledger_id: int) -> Optional[dict]:
        with get_db() as session:
            ledger = session.get(Ledger, ledger_id)
            return ledger.as_dict() if ledger is not None else None

    @staticmethod
    def default_id() -> int:
        """默认账本 id（不存在时创建）"""
        with get_db() as session:
            return ensure_default_ledger(session)

    @staticmethod
    def create(name: str, owner_id: str = "", remark: str = "") -> dict:
        """新建账本；重名由唯一约束兜底转 ConflictError"""
        now = time.time()
        with get_db() as session, translate_unique_violation("账本名已存在"):
            ledger = Ledger(
                name=name,
                owner_id=owner_id,
                is_default=False,
                remark=remark,
                created_at=now,
                updated_at=now,
            )
            session.add(ledger)
            session.flush()
            return ledger.as_dict()

    @staticmethod
    def update_fields(ledger_id: int, fields: dict) -> Optional[dict]:
        """按白名单字段更新账本（名称/备注）；不存在返回 None"""
        if not fields:
            return LedgerDAO.get(ledger_id)
        with get_db() as session, translate_unique_violation("账本名已存在"):
            ledger = session.get(Ledger, ledger_id)
            if ledger is None:
                return None
            for key, value in fields.items():
                setattr(ledger, key, value)
            ledger.updated_at = time.time()
            session.flush()
            return ledger.as_dict()

    @staticmethod
    def move_data(session: Session, from_id: int, to_id: int) -> dict:
        """把某账本下的全部业务数据迁到目标账本（删除账本前的收口）

        必须在调用方的事务内执行（与删除账本同一事务，避免中途失败留下孤儿数据）。
        预算冲突处理见模块 docstring：目标已有同键预算时保留目标、删除来源行。
        """
        moved = {"bills": 0, "budgets": 0, "assets": 0, "dropped_budgets": 0}
        moved["bills"] = session.execute(_update_ledger(Bill, from_id, to_id)).rowcount
        moved["assets"] = session.execute(
            _update_ledger(AssetSnapshot, from_id, to_id)
        ).rowcount
        target_keys = {
            (b.user_id, b.month, b.category)
            for b in session.scalars(select(Budget).where(Budget.ledger_id == to_id))
        }
        for budget in session.scalars(
            select(Budget).where(Budget.ledger_id == from_id)
        ):
            if (budget.user_id, budget.month, budget.category) in target_keys:
                session.delete(budget)
                moved["dropped_budgets"] += 1
                continue
            budget.ledger_id = to_id
            moved["budgets"] += 1
        session.flush()
        return moved

    @staticmethod
    def delete(ledger_id: int) -> Optional[dict]:
        """删除账本并把它的数据并回默认账本；默认账本不可删（返回 None）

        返回 move_data 的并入计数（bills/budgets/assets/dropped_budgets），供
        服务层透出给前端提示「删账本 ≠ 删数据」。
        """
        with get_db() as session:
            ledger = session.get(Ledger, ledger_id)
            if ledger is None or ledger.is_default:
                return None
            moved = LedgerDAO.move_data(
                session, ledger_id, ensure_default_ledger(session)
            )
            session.delete(ledger)
            return moved
