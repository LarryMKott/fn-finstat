"""借贷台账数据访问层（T-7.5）：借出 / 借入与还款记录

静态方法 + get_db 事务块，与其他 DAO 同约定。还款合计由聚合查询给出，
借贷状态（还清即结项）由服务层按合计推导后写回。
"""

from typing import Optional

from sqlalchemy import delete, func, select, update

from app.db.base import get_db
from app.db.models import Loan, LoanPayment


class LoanDAO:
    @staticmethod
    def list_with_progress(user_id: str) -> list[dict]:
        """当前账号全部借贷（按创建时间倒序），附带已还合计与笔数"""
        repaid = func.coalesce(func.sum(LoanPayment.amount), 0.0).label("repaid")
        payment_count = func.count(LoanPayment.id).label("payment_count")
        stmt = (
            select(Loan, repaid, payment_count)
            .outerjoin(LoanPayment, LoanPayment.loan_id == Loan.id)
            .where(Loan.user_id == user_id)
            .group_by(Loan.id)
            .order_by(Loan.created_at.desc(), Loan.id.desc())
        )
        with get_db() as session:
            rows = session.execute(stmt).all()
            return [
                {
                    **r[0].as_dict(),
                    "repaid": float(r[1]),
                    "payment_count": int(r[2]),
                }
                for r in rows
            ]

    @staticmethod
    def get(loan_id: int, user_id: str) -> Optional[dict]:
        """按主键取借贷（仅当前账号），不存在返回 None"""
        with get_db() as session:
            loan = session.scalar(
                select(Loan).where(Loan.id == loan_id, Loan.user_id == user_id)
            )
            return loan.as_dict() if loan is not None else None

    @staticmethod
    def create(user_id: str, fields: dict) -> dict:
        import time

        with get_db() as session:
            loan = Loan(user_id=user_id, created_at=time.time(), **fields)
            session.add(loan)
            session.flush()
            return loan.as_dict()

    @staticmethod
    def update_fields(loan_id: int, user_id: str, fields: dict) -> Optional[dict]:
        """按白名单字段更新借贷，不存在返回 None"""
        if not fields:
            return LoanDAO.get(loan_id, user_id)
        with get_db() as session:
            rowcount = session.execute(
                update(Loan)
                .where(Loan.id == loan_id, Loan.user_id == user_id)
                .values(**fields)
            ).rowcount
        if rowcount == 0:
            return None
        return LoanDAO.get(loan_id, user_id)

    @staticmethod
    def delete(loan_id: int, user_id: str) -> bool:
        """删除借贷及其全部还款记录，返回是否存在"""
        with get_db() as session:
            loan = session.scalar(
                select(Loan).where(Loan.id == loan_id, Loan.user_id == user_id)
            )
            if loan is None:
                return False
            session.execute(delete(LoanPayment).where(LoanPayment.loan_id == loan_id))
            session.delete(loan)
        return True

    @staticmethod
    def repaid_total(loan_id: int, user_id: str) -> float:
        """已还金额合计（借贷不存在时为 0）"""
        with get_db() as session:
            total = session.execute(
                select(func.coalesce(func.sum(LoanPayment.amount), 0.0)).where(
                    LoanPayment.loan_id == Loan.id,
                    Loan.id == loan_id,
                    Loan.user_id == user_id,
                )
            ).scalar()
        return float(total or 0)

    @staticmethod
    def add_payment(loan_id: int, fields: dict) -> dict:
        """追加一条还款记录"""
        import time

        with get_db() as session:
            payment = LoanPayment(loan_id=loan_id, created_at=time.time(), **fields)
            session.add(payment)
            session.flush()
            return payment.as_dict()

    @staticmethod
    def list_payments(loan_id: int) -> list[dict]:
        """还款明细，按还款日期倒序"""
        with get_db() as session:
            rows = session.scalars(
                select(LoanPayment)
                .where(LoanPayment.loan_id == loan_id)
                .order_by(LoanPayment.pay_date.desc(), LoanPayment.id.desc())
            )
            return [p.as_dict() for p in rows]

    @staticmethod
    def get_payment(payment_id: int, loan_id: int) -> Optional[dict]:
        """取一条还款记录（限定所属借贷），不存在返回 None"""
        with get_db() as session:
            payment = session.scalar(
                select(LoanPayment).where(
                    LoanPayment.id == payment_id, LoanPayment.loan_id == loan_id
                )
            )
            return payment.as_dict() if payment is not None else None

    @staticmethod
    def delete_payment(payment_id: int, loan_id: int) -> bool:
        """删除一条还款记录，返回是否存在"""
        with get_db() as session:
            payment = session.scalar(
                select(LoanPayment).where(
                    LoanPayment.id == payment_id, LoanPayment.loan_id == loan_id
                )
            )
            if payment is None:
                return False
            session.delete(payment)
        return True
