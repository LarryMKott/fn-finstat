"""消费分类数据访问层"""
from typing import Optional

from app.db.base import UniqueViolationError, get_db


class CategoryDAO:
    @staticmethod
    def list_all() -> list[dict]:
        with get_db() as db:
            return db.query("SELECT * FROM categories ORDER BY id")

    @staticmethod
    def get_by_name(name: str) -> Optional[dict]:
        with get_db() as db:
            return db.query_one("SELECT * FROM categories WHERE name = ?", (name,))

    @staticmethod
    def get_by_id(category_id: int) -> Optional[dict]:
        with get_db() as db:
            return db.query_one("SELECT * FROM categories WHERE id = ?", (category_id,))

    @staticmethod
    def rename(category_id: int, new_name: str) -> int:
        """重命名分类并同步更新其下流水（单事务），返回同步的流水条数"""
        with get_db() as db:
            old = db.query_one("SELECT name FROM categories WHERE id = ?", (category_id,))["name"]
            db.execute("UPDATE categories SET name = ? WHERE id = ?", (new_name, category_id))
            renamed = db.execute(
                "UPDATE bills SET category = ? WHERE category = ?", (new_name, old)
            )
        return renamed

    @staticmethod
    def delete(category_id: int, fallback: str = "其他") -> int:
        """删除分类，其下流水归入 fallback 分类（单事务），返回迁移的流水条数"""
        with get_db() as db:
            name = db.query_one("SELECT name FROM categories WHERE id = ?", (category_id,))["name"]
            moved = db.execute(
                "UPDATE bills SET category = ? WHERE category = ?", (fallback, name)
            )
            db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        return moved

    @staticmethod
    def create(name: str) -> Optional[int]:
        """新增分类，名称重复返回 None"""
        with get_db() as db:
            try:
                return db.insert("categories", {"name": name})
            except UniqueViolationError:
                return None

    @staticmethod
    def ensure_many(names: list[str]) -> int:
        """批量确保分类存在：缺失分类自动创建（幂等），返回实际新增条数"""
        valid = [n.strip() for n in names if n and n.strip()]
        if not valid:
            return 0
        with get_db() as db:
            before = db.query_one("SELECT COUNT(*) AS n FROM categories")["n"]
            db.insert_ignore("categories", ["name"], [(n,) for n in valid])
            after = db.query_one("SELECT COUNT(*) AS n FROM categories")["n"]
        return after - before
