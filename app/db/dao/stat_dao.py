"""统计查询数据访问层

月度分组统一使用 SUBSTR(tx_time, 1, 7)，SQLite / MySQL / PostgreSQL 行为一致。
"""
from typing import Optional

from app.db.base import get_db
from app.utils.filters import build_filter


class StatDAO:
    @staticmethod
    def _expense_where(
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
    ) -> tuple[str, list]:
        """支出统计专用条件：固定 tx_type='expense' 并叠加可选筛选"""
        where, params = build_filter(start, end, account)
        cond = "tx_type = 'expense'"
        if where:
            where = f"{where} AND {cond}"
        else:
            where = f"WHERE {cond}"
        return where, params

    @staticmethod
    def summary(
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
        tx_type: Optional[str] = None,
    ) -> dict:
        where, params = build_filter(start, end, account, tx_type)
        sql = f"""
            SELECT
                COALESCE(SUM(CASE WHEN tx_type='income'  THEN amount ELSE 0 END), 0) AS income,
                COALESCE(SUM(CASE WHEN tx_type='expense' THEN amount ELSE 0 END), 0) AS expense
            FROM bills {where}
        """
        with get_db() as db:
            return db.query_one(sql, params)

    @staticmethod
    def month_trend(
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
        tx_type: Optional[str] = None,
    ) -> list[dict]:
        where, params = build_filter(start, end, account, tx_type)
        sql = f"""
            SELECT SUBSTR(tx_time, 1, 7) AS month,
                   COALESCE(SUM(CASE WHEN tx_type='income'  THEN amount ELSE 0 END), 0) AS income,
                   COALESCE(SUM(CASE WHEN tx_type='expense' THEN amount ELSE 0 END), 0) AS expense
            FROM bills {where}
            GROUP BY SUBSTR(tx_time, 1, 7)
            ORDER BY month
        """
        with get_db() as db:
            return db.query(sql, params)

    @staticmethod
    def category_pie(
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
    ) -> list[dict]:
        where, params = StatDAO._expense_where(start, end, account)
        sql = f"""
            SELECT category AS name, SUM(amount) AS value
            FROM bills {where}
            GROUP BY category
            ORDER BY value DESC
        """
        with get_db() as db:
            return db.query(sql, params)

    @staticmethod
    def merchant_top(
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        where, params = StatDAO._expense_where(start, end, account)
        sql = f"""
            SELECT merchant, SUM(amount) AS amount, COUNT(*) AS count
            FROM bills {where} AND merchant != ''
            GROUP BY merchant
            ORDER BY amount DESC
            LIMIT ?
        """
        with get_db() as db:
            return db.query(sql, [*params, limit])
