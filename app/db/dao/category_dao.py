"""消费分类数据访问层（SQLAlchemy ORM）"""
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.base import get_db, insert_ignore_rows
from app.db.models import Bill, Category


def _to_dict(category: Category) -> dict:
    return {"id": category.id, "name": category.name}


class CategoryDAO:
    @staticmethod
    def list_all() -> list[dict]:
        with get_db() as session:
            return [_to_dict(c) for c in session.scalars(select(Category).order_by(Category.id))]

    @staticmethod
    def get_by_name(name: str) -> Optional[dict]:
        with get_db() as session:
            category = session.scalar(select(Category).where(Category.name == name))
            return _to_dict(category) if category is not None else None

    @staticmethod
    def get_by_id(category_id: int) -> Optional[dict]:
        with get_db() as session:
            category = session.get(Category, category_id)
            return _to_dict(category) if category is not None else None

    @staticmethod
    def rename(category_id: int, new_name: str) -> int:
        """重命名分类并同步更新其下流水（单事务），返回同步的流水条数"""
        with get_db() as session:
            category = session.get(Category, category_id)
            old = category.name
            category.name = new_name
            renamed = session.execute(
                update(Bill).where(Bill.category == old).values(category=new_name)
            ).rowcount
        return renamed

    @staticmethod
    def delete(category_id: int, fallback: str = "其他") -> int:
        """删除分类，其下流水归入 fallback 分类（单事务），返回迁移的流水条数"""
        with get_db() as session:
            category = session.get(Category, category_id)
            name = category.name
            moved = session.execute(
                update(Bill).where(Bill.category == name).values(category=fallback)
            ).rowcount
            session.execute(delete(Category).where(Category.id == category_id))
        return moved

    @staticmethod
    def create(name: str) -> Optional[int]:
        """新增分类，名称重复返回 None"""
        with get_db() as session:
            try:
                category = Category(name=name)
                session.add(category)
                session.flush()
                return category.id
            except IntegrityError:
                session.rollback()
                return None

    @staticmethod
    def ensure_many(names: list[str]) -> int:
        """批量确保分类存在：缺失分类自动创建（幂等），返回实际新增条数"""
        valid = [n.strip() for n in names if n and n.strip()]
        if not valid:
            return 0
        with get_db() as session:
            before = session.scalar(select(func.count()).select_from(Category))
            insert_ignore_rows(
                session.connection(), Category.__table__, [{"name": n} for n in valid]
            )
            session.flush()
            after = session.scalar(select(func.count()).select_from(Category))
        return max(0, after - before)

    @staticmethod
    def repair_orphans(fallback: str = "其他") -> int:
        """修复孤儿分类：bills.category 不在 categories 表中的流水归入 fallback，返回修复条数

        防止直接操作数据库删除分类后，流水引用到不存在的分类。
        """
        with get_db() as session:
            return session.execute(
                update(Bill)
                .where(Bill.category.not_in(select(Category.name)))
                .values(category=fallback)
            ).rowcount
