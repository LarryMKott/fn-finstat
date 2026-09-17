"""统计查询数据访问层（SQLAlchemy ORM）

月度分组统一使用 substr(tx_time, 1, 7)，SQLite / MySQL / PostgreSQL 行为一致。
"""

from typing import Callable, Optional

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


# 自然语言查询的分组维度白名单：group_by 取值 → 列表达式工厂（T-6.1）
# 服务层已把枚举值约束在白名单内，这里再兜底一次；绝不接受任意表达式文本
_NL_GROUP_EXPRS: dict[str, Callable[[], object]] = {
    "category": lambda: Bill.category,
    "merchant": lambda: Bill.merchant,
    "month": lambda: func.substr(Bill.tx_time, 1, 7),
    "day": lambda: func.substr(Bill.tx_time, 1, 10),
}


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
        max_rows: Optional[int] = None,
    ) -> list[dict]:
        """消费地域识别所需的原始行（仅支出）：商户名 + 备注 + 金额

        地域无法用 SQL 聚合——账单里根本没有地区字段，只能把文本取回应用层
        逐条推断（见 app/utils/region_matcher）。因此这里只取必要列，
        并按金额降序，保证超量截断时优先保留大额流水。

        max_rows 限制取回应用的行数（DBAPI fetchmany 流式拉取，超出行不进内存）：
        大账本全量物化会让每次打开消费地图都把整份支出流水复制一遍。
        是否截断由调用方传入 max_rows+1 后按返回行数判断。
        """
        conds = _expense_criteria(user_id, start, end, account)
        with get_db() as session:
            mapped = session.execute(
                select(Bill.merchant, Bill.remark, Bill.amount)
                .where(*conds)
                .order_by(Bill.amount.desc())
            ).mappings()
            if max_rows is None:
                return [dict(r) for r in mapped]
            return [dict(r) for r in mapped.fetchmany(max_rows)]

    @staticmethod
    def nl_aggregate(
        user_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
        tx_type: Optional[str] = None,
        categories: Optional[list[str]] = None,
        merchants: Optional[list[str]] = None,
        group_by: Optional[str] = None,
        order_by: str = "amount_desc",
        limit: int = 10,
    ) -> dict:
        """自然语言查询聚合（T-6.1，只读）：返回 total/count/rows/truncated

        所有条件均为绑定参数（user_id 强制注入，绝不拼接 SQL 文本）；
        group_by 必须命中 _NL_GROUP_EXPRS 白名单，order_by 命中下方排序白名单，
        非法值由服务层先行约束，此处再兜底取 None/默认值。
        group_by=None 时返回单行汇总（rows 恒空）；分组时取前 limit 条，
        limit+1 探测是否截断（truncated=True 表示还有未取回的分组）。
        """
        conds = build_criteria(
            start,
            end,
            account,
            tx_type,
            user_id=user_id,
            categories=categories,
            merchants=merchants,
        )
        total = func.coalesce(func.sum(Bill.amount), 0.0).label("total")
        cnt = func.count().label("count")
        summary_stmt = select(total, cnt).where(*conds)
        with get_db() as session:
            summary = dict(session.execute(summary_stmt).mappings().one())
        if group_by is None or group_by not in _NL_GROUP_EXPRS:
            return {
                "total": float(summary["total"]),
                "count": int(summary["count"]),
                "rows": [],
                "truncated": False,
            }
        key_expr = _NL_GROUP_EXPRS[group_by]()
        orders = {
            "amount_asc": total.asc(),
            "count_asc": cnt.asc(),
            "count_desc": cnt.desc(),
            "key_asc": key_expr.asc(),
        }
        stmt = (
            select(key_expr.label("key"), total, cnt)
            .where(*conds)
            .group_by(key_expr)
            .order_by(orders.get(order_by, total.desc()))
            .limit(max(1, min(int(limit), 100)) + 1)
        )
        with get_db() as session:
            rows = [dict(r) for r in session.execute(stmt).mappings()]
        truncated = len(rows) > limit
        return {
            "total": float(summary["total"]),
            "count": int(summary["count"]),
            "rows": rows[:limit],
            "truncated": truncated,
        }
