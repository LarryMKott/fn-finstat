"""储蓄目标数据访问层（T-1.4）：目标的增删改查

进度（累计净结余）由服务层从 bills 实时计算，本层只管目标本身。
"""

from typing import Optional

from sqlalchemy import select, update

from app.db.base import get_db
from app.db.models import SavingsGoal


class SavingsGoalDAO:
    @staticmethod
    def list_goals(user_id: str) -> list[dict]:
        """当前账号全部目标，按创建时间倒序"""
        with get_db() as session:
            rows = session.scalars(
                select(SavingsGoal)
                .where(SavingsGoal.user_id == user_id)
                .order_by(SavingsGoal.created_at.desc(), SavingsGoal.id.desc())
            )
            return [g.as_dict() for g in rows]

    @staticmethod
    def get(goal_id: int, user_id: str) -> Optional[dict]:
        """按主键取目标（仅当前账号），不存在返回 None"""
        with get_db() as session:
            goal = session.scalar(
                select(SavingsGoal).where(
                    SavingsGoal.id == goal_id, SavingsGoal.user_id == user_id
                )
            )
            return goal.as_dict() if goal is not None else None

    @staticmethod
    def create(user_id: str, fields: dict) -> dict:
        import time

        with get_db() as session:
            goal = SavingsGoal(user_id=user_id, created_at=time.time(), **fields)
            session.add(goal)
            session.flush()
            return goal.as_dict()

    @staticmethod
    def update_fields(goal_id: int, user_id: str, fields: dict) -> Optional[dict]:
        """按白名单字段更新目标，不存在返回 None"""
        if not fields:
            return SavingsGoalDAO.get(goal_id, user_id)
        with get_db() as session:
            rowcount = session.execute(
                update(SavingsGoal)
                .where(SavingsGoal.id == goal_id, SavingsGoal.user_id == user_id)
                .values(**fields)
            ).rowcount
        if rowcount == 0:
            return None
        return SavingsGoalDAO.get(goal_id, user_id)

    @staticmethod
    def delete(goal_id: int, user_id: str) -> bool:
        """删除目标（不影响流水），返回是否存在"""
        with get_db() as session:
            goal = session.scalar(
                select(SavingsGoal).where(
                    SavingsGoal.id == goal_id, SavingsGoal.user_id == user_id
                )
            )
            if goal is None:
                return False
            session.delete(goal)
        return True
