"""消费分类数据访问层"""
import sqlite3
from typing import Optional

from app.db.base import get_db


class CategoryDAO:
    @staticmethod
    def list_all() -> list[dict]:
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM categories ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def get_by_name(name: str) -> Optional[dict]:
        with get_db() as conn:
            row = conn.execute("SELECT * FROM categories WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def create(name: str) -> Optional[int]:
        """新增分类，名称重复返回 None"""
        with get_db() as conn:
            try:
                cur = conn.execute("INSERT INTO categories(name) VALUES (?)", (name,))
                return cur.lastrowid
            except sqlite3.IntegrityError:
                return None

    @staticmethod
    def ensure_many(names: list[str]) -> int:
        """批量确保分类存在：缺失分类自动创建（幂等），返回实际新增条数"""
        valid = [n.strip() for n in names if n and n.strip()]
        if not valid:
            return 0
        with get_db() as conn:
            before = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
            conn.executemany(
                "INSERT OR IGNORE INTO categories(name) VALUES (?)",
                [(n,) for n in valid],
            )
            after = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        return after - before
