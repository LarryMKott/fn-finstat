"""统计查询数据访问层"""
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
        with get_db() as conn:
            row = conn.execute(sql, params).fetchone()
        return dict(row)

    @staticmethod
    def month_trend(
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
        tx_type: Optional[str] = None,
    ) -> list[dict]:
        where, params = build_filter(start, end, account, tx_type)
        sql = f"""
            SELECT strftime('%Y-%m', tx_time) AS month,
                   COALESCE(SUM(CASE WHEN tx_type='income'  THEN amount ELSE 0 END), 0) AS income,
                   COALESCE(SUM(CASE WHEN tx_type='expense' THEN amount ELSE 0 END), 0) AS expense
            FROM bills {where}
            GROUP BY month
            ORDER BY month
        """
        with get_db() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

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
        with get_db() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

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
        with get_db() as conn:
            rows = conn.execute(sql, [*params, limit]).fetchall()
        return [dict(r) for r in rows]
