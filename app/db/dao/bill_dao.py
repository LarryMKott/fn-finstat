"""账单流水数据访问层"""
from typing import Optional

from app.db.base import get_db
from app.utils.filters import build_filter

_COLS = ["tx_time", "account", "tx_type", "merchant", "amount", "category", "tx_id", "remark"]
_SORTABLE = {"tx_time", "account", "tx_type", "merchant", "amount", "category", "remark"}


def _normalize(rec: dict) -> dict:
    """归一化记录：空交易号转 NULL（UNIQUE 允许多个 NULL，空串全局只允许一条）"""
    values = dict(rec)
    if not values.get("tx_id"):
        values["tx_id"] = None
    return values


def _row(rec: dict) -> tuple:
    """按列序转元组，供批量插入使用"""
    values = _normalize(rec)
    return tuple(values[c] for c in _COLS)


def _row_dict(data: dict) -> dict:
    """返回归一化后的字典，供单行插入使用"""
    return _normalize(data)


class BillDAO:
    @staticmethod
    def insert_many(records: list[dict]) -> int:
        """批量插入，交易号(tx_id)唯一去重；返回实际新增条数

        优先使用 executemany 的 rowcount（MySQL 准确）；
        驱动不支持时（SQLite/PG 的 rowcount 可能为 -1）回退到事务内 COUNT 差值。
        整个操作在单事务内完成，COUNT 差值在事务隔离下并发安全。
        """
        with get_db() as db:
            before = db.query_one("SELECT COUNT(*) AS n FROM bills")["n"]
            inserted = db.insert_ignore("bills", _COLS, [_row(r) for r in records])
            if inserted < 0:
                after = db.query_one("SELECT COUNT(*) AS n FROM bills")["n"]
                inserted = after - before
        return inserted

    @staticmethod
    def list_bills(
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
        tx_type: Optional[str] = None,
        category: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "tx_time",
        order: str = "desc",
    ) -> tuple[int, list[dict]]:
        """多条件分页查询，支持指定字段排序（字段经服务层白名单校验）"""
        where, params = build_filter(start, end, account, tx_type, category)
        sort_col = sort_by if sort_by in _SORTABLE else "tx_time"
        direction = "ASC" if str(order).lower() == "asc" else "DESC"
        with get_db() as db:
            total = db.query_one(f"SELECT COUNT(*) AS n FROM bills {where}", params)["n"]
            rows = db.query(
                f"SELECT * FROM bills {where} ORDER BY {sort_col} {direction}, id DESC LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            )
        return total, rows

    @staticmethod
    def get_by_id(bill_id: int) -> Optional[dict]:
        with get_db() as db:
            return db.query_one("SELECT * FROM bills WHERE id = ?", (bill_id,))

    @staticmethod
    def tx_id_exists(tx_id: str, exclude_id: Optional[int] = None) -> bool:
        """交易号是否已存在；exclude_id 用于编辑时排除自身"""
        with get_db() as db:
            if exclude_id is not None:
                row = db.query_one(
                    "SELECT 1 AS one FROM bills WHERE tx_id = ? AND id != ?", (tx_id, exclude_id)
                )
            else:
                row = db.query_one("SELECT 1 AS one FROM bills WHERE tx_id = ?", (tx_id,))
        return row is not None

    @staticmethod
    def create(data: dict) -> int:
        with get_db() as db:
            return db.insert("bills", _row_dict(data))

    @staticmethod
    def update(bill_id: int, fields: dict) -> bool:
        """按白名单字段更新；fields 由服务层校验后传入"""
        if not fields:
            return False
        sets = ", ".join(f"{k} = ?" for k in fields)
        with get_db() as db:
            rowcount = db.execute(
                f"UPDATE bills SET {sets} WHERE id = ?", [*fields.values(), bill_id]
            )
        return rowcount > 0

    @staticmethod
    def count_by_category(category: str) -> int:
        """某分类下的流水条数"""
        with get_db() as db:
            return db.query_one("SELECT COUNT(*) AS n FROM bills WHERE category = ?", (category,))["n"]

    @staticmethod
    def delete(bill_id: int) -> bool:
        with get_db() as db:
            rowcount = db.execute("DELETE FROM bills WHERE id = ?", (bill_id,))
        return rowcount > 0
