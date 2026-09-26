"""消费分类数据访问层（SQLAlchemy ORM）"""

from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.config import DEFAULT_CATEGORY
from app.core.errors import ErrorCode, NotFoundError
from app.db.base import (
    get_db,
    insert_ignore_rows,
    is_unique_violation,
    translate_unique_violation,
)
from app.db.models import Bill, Category, CategoryKeyword


def _to_dict(category: Category) -> dict:
    """ORM 对象转纯字典，隔离服务层与 ORM 实体"""
    return {
        "id": category.id,
        "name": category.name,
        "parent_id": category.parent_id,
        "source": category.source,
        "created_at": category.created_at,
    }


class CategoryDAO:
    @staticmethod
    def list_all() -> list[dict]:
        """全部分类，按 id 升序（保持创建顺序）"""
        with get_db() as session:
            return [
                _to_dict(c)
                for c in session.scalars(select(Category).order_by(Category.id))
            ]

    @staticmethod
    def get_by_name(name: str) -> Optional[dict]:
        """按名称查分类，不存在返回 None"""
        with get_db() as session:
            category = session.scalar(select(Category).where(Category.name == name))
            return _to_dict(category) if category is not None else None

    @staticmethod
    def get_by_id(category_id: int) -> Optional[dict]:
        """按主键查分类，不存在返回 None"""
        with get_db() as session:
            category = session.get(Category, category_id)
            return _to_dict(category) if category is not None else None

    @staticmethod
    def rename(category_id: int, new_name: str) -> int:
        """重命名分类并同步更新其下流水（单事务），返回同步的流水条数

        并发下同名分类可能越过预检查，由唯一约束兜底（转 ConflictError）。
        """
        with get_db() as session:
            with translate_unique_violation(
                "分类已存在", code=ErrorCode.CATEGORY_INVALID
            ):
                category = session.get(Category, category_id)
                # 服务层的存在性预检查与这里不在同一事务：并发删除后 get 返回
                # None，需显式报 404 而不是让 category.name 抛 AttributeError → 500
                if category is None:
                    raise NotFoundError("分类不存在")
                old = category.name
                category.name = new_name
                renamed = session.execute(
                    update(Bill).where(Bill.category == old).values(category=new_name)
                ).rowcount
        return renamed

    @staticmethod
    def delete(category_id: int, fallback: str = DEFAULT_CATEGORY) -> int:
        """删除分类，其下流水归入 fallback 分类（单事务），返回迁移的流水条数

        同事务级联清理该分类的关键词（category_keywords.category_id 指向本表，
        库层无外键约束，由 DAO 显式删）。
        """
        with get_db() as session:
            category = session.get(Category, category_id)
            if category is None:  # 并发删除竞态：显式 404（见 rename 内注释）
                raise NotFoundError("分类不存在")
            name = category.name
            moved = session.execute(
                update(Bill).where(Bill.category == name).values(category=fallback)
            ).rowcount
            session.execute(
                delete(CategoryKeyword).where(
                    CategoryKeyword.category_id == category_id
                )
            )
            session.execute(delete(Category).where(Category.id == category_id))
        return moved

    @staticmethod
    def create(
        name: str, parent_id: Optional[int] = None, source: str = "manual"
    ) -> Optional[int]:
        """新增分类，名称重复返回 None；其余完整性冲突（非唯一约束）原样抛出"""
        with get_db() as session:
            try:
                category = Category(name=name, parent_id=parent_id, source=source)
                session.add(category)
                session.flush()
                return category.id
            except IntegrityError as exc:
                session.rollback()
                if not is_unique_violation(exc):
                    raise
                return None

    @staticmethod
    def has_children(category_id: int) -> bool:
        """是否存在以该分类为父的分类（删除保护：有子分类不得删）"""
        with get_db() as session:
            return (
                session.scalar(
                    select(Category.id)
                    .where(Category.parent_id == category_id)
                    .limit(1)
                )
                is not None
            )

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
    def repair_orphans(fallback: str = DEFAULT_CATEGORY) -> int:
        """修复孤儿分类：bills.category 不在 categories 表中的流水归入 fallback，返回修复条数

        防止直接操作数据库删除分类后，流水引用到不存在的分类。
        """
        with get_db() as session:
            return session.execute(
                update(Bill)
                .where(Bill.category.not_in(select(Category.name)))
                .values(category=fallback)
            ).rowcount
