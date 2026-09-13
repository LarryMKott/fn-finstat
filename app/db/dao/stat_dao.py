"""统计查询数据访问层（SQLAlchemy ORM）

月度分组统一使用 substr(tx_time, 1, 7)，SQLite / MySQL / PostgreSQL 行为一致。
"""

from typing import Optional

from sqlalchemy import case, func, select

from app.db.base import get_db
from app.db.models import Bill
from app.utils.filters import build_criteria


def _expense_criteria(
    user_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    account: Optional[str] = None,
) -> list:
    """支出统计专用条件：固定 tx_type='expense' 并叠加可选筛选"""
    return build_criteria(start, end, account, tx_type="expense", user_id=user_id)


class StatDAO:
    @staticmethod
    def summary(
        user_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
        tx_type: Optional[str] = None,
    ) -> dict:
        """区间收支汇总：income/expense 按 tx_type 分别条件求和，转账不计入任一侧"""
        conds = build_criteria(start, end, account, tx_type, user_id=user_id)
        stmt = select(
            func.coalesce(
                func.sum(case((Bill.tx_type == "income", Bill.amount), else_=0.0)), 0.0
            ).label("income"),
            func.coalesce(
                func.sum(case((Bill.tx_type == "expense", Bill.amount), else_=0.0)), 0.0
            ).label("expense"),
        ).where(*conds)
        with get_db() as session:
            return dict(session.execute(stmt).mappings().one())

    @staticmethod
    def month_trend(
        user_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
        tx_type: Optional[str] = None,
    ) -> list[dict]:
        """月度收支趋势：按 tx_time 前 7 位（YYYY-MM）分组，月份升序返回"""
        conds = build_criteria(start, end, account, tx_type, user_id=user_id)
        month = func.substr(Bill.tx_time, 1, 7).label("month")
        stmt = (
            select(
                month,
                func.coalesce(
                    func.sum(case((Bill.tx_type == "income", Bill.amount), else_=0.0)),
                    0.0,
                ).label("income"),
                func.coalesce(
                    func.sum(case((Bill.tx_type == "expense", Bill.amount), else_=0.0)),
                    0.0,
                ).label("expense"),
            )
            .where(*conds)
            .group_by(month)
            .order_by(month)
        )
        with get_db() as session:
            return [dict(r) for r in session.execute(stmt).mappings()]

    @staticmethod
    def category_pie(
        user_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
    ) -> list[dict]:
        """分类支出占比（饼图）：仅统计支出，按分类汇总金额后降序返回"""
        conds = _expense_criteria(user_id, start, end, account)
        total = func.sum(Bill.amount).label("value")
        stmt = (
            select(Bill.category.label("name"), total)
            .where(*conds)
            .group_by(Bill.category)
            .order_by(total.desc())
        )
        with get_db() as session:
            return [dict(r) for r in session.execute(stmt).mappings()]

    @staticmethod
    def merchant_top(
        user_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        """商户支出 TOP N：按商户汇总支出金额与笔数，金额降序取前 limit 条（排除空商户）"""
        conds = _expense_criteria(user_id, start, end, account)
        total = func.sum(Bill.amount).label("amount")
        stmt = (
            select(
                Bill.merchant,
                total,
                func.count().label("count"),
            )
            .where(*conds, Bill.merchant != "")
            .group_by(Bill.merchant)
            .order_by(total.desc())
            .limit(limit)
        )
        with get_db() as session:
            return [dict(r) for r in session.execute(stmt).mappings()]

    @staticmethod
    def daily_totals(
        user_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
    ) -> list[dict]:
        """按日收支汇总（日历热力图）：按 tx_time 前 10 位（YYYY-MM-DD）分组，日期升序

        仅返回有流水的日期，无流水的日期由前端按 0 处理。
        """
        conds = build_criteria(start, end, account, user_id=user_id)
        day = func.substr(Bill.tx_time, 1, 10).label("date")
        stmt = (
            select(
                day,
                func.coalesce(
                    func.sum(case((Bill.tx_type == "income", Bill.amount), else_=0.0)),
                    0.0,
                ).label("income"),
                func.coalesce(
                    func.sum(case((Bill.tx_type == "expense", Bill.amount), else_=0.0)),
                    0.0,
                ).label("expense"),
            )
            .where(*conds)
            .group_by(day)
            .order_by(day)
        )
        with get_db() as session:
            return [dict(r) for r in session.execute(stmt).mappings()]

    @staticmethod
    def region_rows(
        user_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
    ) -> list[dict]:
        """消费地域识别所需的原始行（仅支出）：商户名 + 备注 + 金额

        地域无法用 SQL 聚合——账单里根本没有地区字段，只能把文本取回应用层
        逐条推断（见 app/utils/region_matcher）。因此这里只取必要列，
        并按金额降序，保证超量截断时优先保留大额流水。
        """
        conds = _expense_criteria(user_id, start, end, account)
        stmt = (
            select(Bill.merchant, Bill.remark, Bill.amount)
            .where(*conds)
            .order_by(Bill.amount.desc())
        )
        with get_db() as session:
            return [dict(r) for r in session.execute(stmt).mappings()]
