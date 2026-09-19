"""报销单数据访问层（T-7.4 报销 / 垫付工作流）

报销单（reimbursements）与流水（bills.reimb_id）的关联写操作都收在本层；
bills.reimbursed 布尔随关联同步（挂单置 1、摘除置 0），保证既有报销筛选
与导出列零破坏。静态方法 + get_db 事务块，与其他 DAO 同约定。
"""

from typing import Optional

from sqlalchemy import func, select, update

from app.db.base import get_db, in_chunks
from app.db.models import Bill, Reimbursement


class ReimbDAO:
    @staticmethod
    def list_with_stats(user_id: str) -> list[dict]:
        """当前账号全部报销单，按创建时间倒序；附带关联流水笔数与金额合计"""
        bill_count = func.count(Bill.id).label("bill_count")
        total_amount = func.coalesce(func.sum(Bill.amount), 0.0).label("total_amount")
        stmt = (
            select(Reimbursement, bill_count, total_amount)
            .outerjoin(
                Bill,
                (Bill.reimb_id == Reimbursement.id) & (Bill.deleted.is_(False)),
            )
            .where(Reimbursement.user_id == user_id)
            .group_by(Reimbursement.id)
            .order_by(Reimbursement.created_at.desc(), Reimbursement.id.desc())
        )
        with get_db() as session:
            rows = session.execute(stmt).all()
            return [
                {
                    **r[0].as_dict(),
                    "bill_count": int(r[1]),
                    "total_amount": float(r[2]),
                }
                for r in rows
            ]

    @staticmethod
    def get(claim_id: int, user_id: str) -> Optional[dict]:
        """按主键取报销单（仅当前账号），不存在返回 None"""
        with get_db() as session:
            claim = session.scalar(
                select(Reimbursement).where(
                    Reimbursement.id == claim_id, Reimbursement.user_id == user_id
                )
            )
            return claim.as_dict() if claim is not None else None

    @staticmethod
    def create(user_id: str, title: str, note: str) -> dict:
        """新建报销单，固定待提交状态"""
        import time

        with get_db() as session:
            claim = Reimbursement(
                user_id=user_id,
                title=title,
                status="pending",
                note=note,
                created_at=time.time(),
            )
            session.add(claim)
            session.flush()
            return claim.as_dict()

    @staticmethod
    def update_fields(claim_id: int, user_id: str, fields: dict) -> Optional[dict]:
        """按白名单字段更新报销单，不存在返回 None"""
        if not fields:
            return ReimbDAO.get(claim_id, user_id)
        with get_db() as session:
            rowcount = session.execute(
                update(Reimbursement)
                .where(Reimbursement.id == claim_id, Reimbursement.user_id == user_id)
                .values(**fields)
            ).rowcount
        if rowcount == 0:
            return None
        return ReimbDAO.get(claim_id, user_id)

    @staticmethod
    def delete(claim_id: int, user_id: str) -> bool:
        """删除报销单并摘除其下流水（claim_id 置空、报销标记复位），返回是否存在"""
        with get_db() as session:
            claim = session.scalar(
                select(Reimbursement).where(
                    Reimbursement.id == claim_id, Reimbursement.user_id == user_id
                )
            )
            if claim is None:
                return False
            session.execute(
                update(Bill)
                .where(Bill.reimb_id == claim_id)
                .values(reimb_id=None, reimbursed=False)
            )
            session.delete(claim)
        return True

    @staticmethod
    def attach_bills(claim_id: int, user_id: str, ids: list[int]) -> int:
        """把流水挂到报销单（置 reimb_id + 同步报销标记），返回受影响条数"""
        changed = 0
        with get_db() as session:
            for chunk in in_chunks(ids):
                changed += session.execute(
                    update(Bill)
                    .where(Bill.id.in_(chunk), Bill.user_id == user_id)
                    .values(reimb_id=claim_id, reimbursed=True)
                ).rowcount
        return changed

    @staticmethod
    def detach_bills(claim_id: int, user_id: str, ids: list[int]) -> int:
        """从报销单摘除流水（仅限本单内的流水），返回受影响条数"""
        changed = 0
        with get_db() as session:
            for chunk in in_chunks(ids):
                changed += session.execute(
                    update(Bill)
                    .where(
                        Bill.id.in_(chunk),
                        Bill.user_id == user_id,
                        Bill.reimb_id == claim_id,
                    )
                    .values(reimb_id=None, reimbursed=False)
                ).rowcount
        return changed

    @staticmethod
    def list_bills(claim_id: int, user_id: str) -> list[dict]:
        """报销单内流水（未删除），按交易时间倒序"""
        conds = [
            Bill.reimb_id == claim_id,
            Bill.user_id == user_id,
            Bill.deleted.is_(False),
        ]
        with get_db() as session:
            stmt = (
                select(Bill).where(*conds).order_by(Bill.tx_time.desc(), Bill.id.desc())
            )
            return [b.as_dict() for b in session.scalars(stmt)]

    @staticmethod
    def clear_claims(user_id: str, ids: list[int]) -> int:
        """流水进入回收站时摘除报销关联（仅当前账号），返回受影响条数"""
        if not ids:
            return 0
        changed = 0
        with get_db() as session:
            for chunk in in_chunks(ids):
                changed += session.execute(
                    update(Bill)
                    .where(Bill.id.in_(chunk), Bill.user_id == user_id)
                    .values(reimb_id=None, reimbursed=False)
                ).rowcount
        return changed
