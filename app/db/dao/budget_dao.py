"""月度预算数据访问层（SQLAlchemy ORM，按 user_id 归属账号；category 空串 = 总预算）"""

from typing import Optional

from sqlalchemy import select

from app.db.base import get_db, translate_unique_violation
from app.db.models import Budget


def _to_dict(budget: Budget) -> dict:
    return budget.as_dict()


class BudgetDAO:
    @staticmethod
    def list_month(user_id: str, month: str) -> list[dict]:
        """某月全部预算（总预算在前、分类预算按 id 升序）"""
        with get_db() as session:
            rows = session.scalars(
                select(Budget)
                .where(Budget.user_id == user_id, Budget.month == month)
                .order_by(Budget.category, Budget.id)
            )
            return [_to_dict(b) for b in rows]

    @staticmethod
    def get_by_scope(user_id: str, month: str, category: str) -> Optional[dict]:
        """按唯一键（账号+月份+分类）查预算"""
        with get_db() as session:
            budget = session.scalar(
                select(Budget).where(
                    Budget.user_id == user_id,
                    Budget.month == month,
                    Budget.category == category,
                )
            )
            return _to_dict(budget) if budget is not None else None

    @staticmethod
    def upsert(user_id: str, month: str, category: str, amount: float) -> dict:
        """按唯一键插入或更新预算金额

        唯一约束兜底并发：预检查与插入之间另一方可能已插入同键预算，
        IntegrityError 翻译为 ConflictError（与 bills/categories 写路径同策略）。
        """
        with get_db() as session, translate_unique_violation("预算已存在"):
            budget = session.scalar(
                select(Budget).where(
                    Budget.user_id == user_id,
                    Budget.month == month,
                    Budget.category == category,
                )
            )
            if budget is None:
                budget = Budget(
                    user_id=user_id, month=month, category=category, amount=amount
                )
                session.add(budget)
            else:
                budget.amount = amount
            session.flush()
            return _to_dict(budget)

    @staticmethod
    def delete(budget_id: int, user_id: str) -> bool:
        """删除预算（仅当前账号），返回是否存在"""
        with get_db() as session:
            budget = session.scalar(
                select(Budget).where(Budget.id == budget_id, Budget.user_id == user_id)
            )
            if budget is None:
                return False
            session.delete(budget)
        return True
