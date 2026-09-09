"""账单流水数据访问层"""
from typing import Optional

from app.db.base import get_db
from app.utils.filters import build_filter


class BillDAO:
    @staticmethod
    def insert_many(records: list[dict]) -> int:
        """批量插入，交易号(tx_id)唯一去重；返回实际新增条数"""
        with get_db() as conn:
            before = conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0]
            conn.executemany(
                """
                INSERT OR IGNORE INTO bills
                    (tx_time, account, tx_type, merchant, amount, category, tx_id, remark)
                VALUES
                    (:tx_time, :account, :tx_type, :merchant, :amount, :category, :tx_id, :remark)
                """,
                records,
            )
            after = conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0]
        return after - before

    @staticmethod
    def list_bills(
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
        tx_type: Optional[str] = None,
        category: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[dict]]:
        """多条件分页查询，按交易时间倒序"""
        where, params = build_filter(start, end, account, tx_type, category)
        with get_db() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM bills {where}", params).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM bills {where} ORDER BY tx_time DESC, id DESC LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        return total, [dict(r) for r in rows]

    @staticmethod
    def get_by_id(bill_id: int) -> Optional[dict]:
        with get_db() as conn:
            row = conn.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def tx_id_exists(tx_id: str, exclude_id: Optional[int] = None) -> bool:
        """交易号是否已存在；exclude_id 用于编辑时排除自身"""
        with get_db() as conn:
            if exclude_id is not None:
                row = conn.execute(
                    "SELECT 1 FROM bills WHERE tx_id = ? AND id != ?", (tx_id, exclude_id)
                ).fetchone()
            else:
                row = conn.execute("SELECT 1 FROM bills WHERE tx_id = ?", (tx_id,)).fetchone()
        return row is not None

    @staticmethod
    def create(data: dict) -> int:
        with get_db() as conn:
            cur = conn.execute(
                """
                INSERT INTO bills (tx_time, account, tx_type, merchant, amount, category, tx_id, remark)
                VALUES (:tx_time, :account, :tx_type, :merchant, :amount, :category, :tx_id, :remark)
                """,
                data,
            )
            return cur.lastrowid

    @staticmethod
    def update(bill_id: int, fields: dict) -> bool:
        """按白名单字段更新；fields 由服务层校验后传入"""
        if not fields:
            return False
        sets = ", ".join(f"{k} = ?" for k in fields)
        with get_db() as conn:
            cur = conn.execute(
                f"UPDATE bills SET {sets} WHERE id = ?", [*fields.values(), bill_id]
            )
            return cur.rowcount > 0

    @staticmethod
    def delete(bill_id: int) -> bool:
        with get_db() as conn:
            cur = conn.execute("DELETE FROM bills WHERE id = ?", (bill_id,))
            return cur.rowcount > 0
