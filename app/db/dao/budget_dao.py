"""月度预算数据访问层（SQLAlchemy ORM，按 user_id 归属账号；category 空串 = 总预算）

账本维度（T-7.1）：budgets 唯一键为 (user_id, ledger_id, month, category)，
同一账号在不同账本下可各设一份预算。读路径 ledger_id=None 表示不按账本过滤，
写路径 ledger_id=None 表示落到默认账本——两者在「全库只有默认账本」时等价，
因此不传 ledger_id 的旧调用行为与升级前完全一致。
"""

from typing import Optional

from sqlalchemy import delete, func, select

from app.core.constants import family_scope_user
from app.db.base import get_db, translate_unique_violation
from app.db.ledgers import resolve_ledger_id
from app.db.models import DEFAULT_LEDGER_ID, Budget


class BudgetDAO:
    @staticmethod
    def list_month(
        user_id: str, month: str, ledger_id: Optional[int] = None
    ) -> list[dict]:
        """某月全部预算（总预算在前、分类预算按 id 升序）

        ledger_id 为 None 时不按账本过滤（旧调用行为不变）。
        """
        with get_db() as session:
            conds = [Budget.user_id == user_id, Budget.month == month]
            if ledger_id is not None:
                conds.append(Budget.ledger_id == ledger_id)
            rows = session.scalars(
                select(Budget).where(*conds).order_by(Budget.category, Budget.id)
            )
            return [b.as_dict() for b in rows]

    @staticmethod
    def get_by_scope(
        user_id: str,
        month: str,
        category: str,
        ledger_id: Optional[int] = None,
    ) -> Optional[dict]:
        """按唯一键（账号+账本+月份+分类）查预算；ledger_id 为 None 时取默认账本"""
        with get_db() as session:
            ledger_id = resolve_ledger_id(session, ledger_id)
            budget = session.scalar(
                select(Budget).where(
                    Budget.user_id == user_id,
                    Budget.ledger_id == ledger_id,
                    Budget.month == month,
                    Budget.category == category,
                )
            )
            return budget.as_dict() if budget is not None else None

    @staticmethod
    def upsert(
        user_id: str,
        month: str,
        category: str,
        amount: float,
        ledger_id: Optional[int] = None,
    ) -> dict:
        """按唯一键插入或更新预算金额，ledger_id 为 None 时落到默认账本

        唯一约束兜底并发：预检查与插入之间另一方可能已插入同键预算，
        IntegrityError 翻译为 ConflictError（与 bills/categories 写路径同策略）。
        """
        with get_db() as session, translate_unique_violation("预算已存在"):
            ledger_id = resolve_ledger_id(session, ledger_id)
            budget = session.scalar(
                select(Budget).where(
                    Budget.user_id == user_id,
                    Budget.ledger_id == ledger_id,
                    Budget.month == month,
                    Budget.category == category,
                )
            )
            if budget is None:
                budget = Budget(
                    user_id=user_id,
                    ledger_id=ledger_id,
                    month=month,
                    category=category,
                    amount=amount,
                )
                session.add(budget)
            else:
                budget.amount = amount
            session.flush()
            return budget.as_dict()

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

    # ---- 家庭预算（T-7.3）----

    # 说明：家庭预算行的 user_id 用合成属主（constants.family_scope_user），
    # 既有 (user_id, ledger_id, month, category) 唯一键天然按家庭去重，且与
    # 创建者本人的个人预算互不冲突；ledger_id 列 NOT NULL，家庭预算不按账本
    # 维度，统一落默认账本占位（见 Budget 模型 docstring）。

    @staticmethod
    def list_family(family_id: int, month: str) -> list[dict]:
        """某月家庭预算行（总预算在前、分类预算按 id 升序）"""
        with get_db() as session:
            rows = session.scalars(
                select(Budget)
                .where(
                    Budget.family_id == family_id,
                    Budget.month == month,
                )
                .order_by(Budget.category, Budget.id)
            )
            return [b.as_dict() for b in rows]

    @staticmethod
    def upsert_family(family_id: int, month: str, category: str, amount: float) -> dict:
        """按（家庭 + 月份 + 分类）插入或更新家庭预算

        唯一约束兜底并发：并发重复提交由 IntegrityError 翻译为 ConflictError。
        """
        with get_db() as session, translate_unique_violation("家庭预算已存在"):
            budget = session.scalar(
                select(Budget).where(
                    Budget.family_id == family_id,
                    Budget.month == month,
                    Budget.category == category,
                )
            )
            if budget is None:
                budget = Budget(
                    user_id=family_scope_user(family_id),
                    family_id=family_id,
                    ledger_id=DEFAULT_LEDGER_ID,
                    month=month,
                    category=category,
                    amount=amount,
                )
                session.add(budget)
            else:
                budget.amount = amount
            session.flush()
            return budget.as_dict()

    @staticmethod
    def delete_in_family(budget_id: int, family_id: int) -> bool:
        """删除本家庭的预算行；不存在或不属于该家庭时返回 False"""
        with get_db() as session:
            budget = session.scalar(
                select(Budget).where(
                    Budget.id == budget_id, Budget.family_id == family_id
                )
            )
            if budget is None:
                return False
            session.delete(budget)
        return True

    @staticmethod
    def delete_family_budgets(family_id: int) -> int:
        """解散家庭时清空其全部预算行，返回删除条数"""
        with get_db() as session:
            return session.execute(
                delete(Budget).where(Budget.family_id == family_id)
            ).rowcount

    @staticmethod
    def category_amount(
        user_id: str, month: str, category: str, ledger_id: Optional[int] = None
    ) -> float:
        """某月某分类的预算金额合计（ledger_id 为 None 时合计全部账本）

        供预算建议的 current_budget 使用，与「建议值按账本口径统计支出」保持
        同一口径：不传账本 = 全部账本（读路径约定），传账本 = 仅该账本。
        """
        conds = [
            Budget.user_id == user_id,
            Budget.month == month,
            Budget.category == category,
        ]
        if ledger_id is not None:
            conds.append(Budget.ledger_id == ledger_id)
        with get_db() as session:
            total = session.scalar(select(func.sum(Budget.amount)).where(*conds))
        return float(total or 0)
