"""分类关键词数据访问层（SQLAlchemy ORM，v1.1 CAP-1 底座）

关键词全局共享（分类本身全局共用），(category_id, keyword) 唯一去重；
同词跨分类合法（如「会员」既是娱乐也是购物），匹配歧义由服务层的
「长词优先、同长先到先得」排序消解。
"""

import time

from sqlalchemy import delete, select, update

from app.db.base import get_db, insert_ignore_rows
from app.db.models import Category, CategoryKeyword


class CategoryKeywordDAO:
    @staticmethod
    def list_by_category(category_id: int, enabled_only: bool = False) -> list[dict]:
        """某分类的关键词列表（管理页抽屉用），按创建时间升序"""
        with get_db() as session:
            stmt = select(CategoryKeyword).where(
                CategoryKeyword.category_id == category_id
            )
            if enabled_only:
                stmt = stmt.where(CategoryKeyword.enabled.is_(True))
            stmt = stmt.order_by(CategoryKeyword.created_at, CategoryKeyword.id)
            return [k.as_dict() for k in session.scalars(stmt)]

    @staticmethod
    def list_enabled_with_category() -> list[dict]:
        """全部启用中的关键词（附分类名，匹配用），按 keyword 长度降序、同长按 id

        排序放 Python 侧：三方言的字符长度函数语义不一（MySQL 的 LENGTH 按
        字节计会错排中文），而本表量级为百行、每次导入批量调用一次，内存排序
        足够且行为绝对一致。
        """
        with get_db() as session:
            stmt = (
                select(CategoryKeyword, Category.name)
                .join(Category, CategoryKeyword.category_id == Category.id)
                .where(CategoryKeyword.enabled.is_(True))
                .order_by(CategoryKeyword.id)
            )
            rows = [
                {**kw.as_dict(), "category": name}
                for kw, name in session.execute(stmt).all()
            ]
        rows.sort(key=lambda r: (-len(r["keyword"]), r["id"]))
        return rows

    @staticmethod
    def get(keyword_id: int) -> dict | None:
        with get_db() as session:
            row = session.get(CategoryKeyword, keyword_id)
            return row.as_dict() if row is not None else None

    @staticmethod
    def create_many(
        category_id: int, keywords: list[str], source: str = "manual"
    ) -> int:
        """批量新增（批内去重 + 唯一键冲突跳过），返回实际入库条数

        传参前由服务层完成清洗（长度边界/非法字符），本方法不做校验。
        """
        # 批内按大小写不敏感去重，保持首个出现的写法
        seen: set[str] = set()
        rows = []
        for word in keywords:
            key = word.lower()
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "category_id": category_id,
                    "keyword": word,
                    "source": source,
                    "enabled": True,
                    "created_at": time.time(),
                }
            )
        if not rows:
            return 0
        with get_db() as session:
            inserted = insert_ignore_rows(
                session.connection(), CategoryKeyword.__table__, rows
            )
        return max(0, inserted)

    @staticmethod
    def set_enabled(keyword_id: int, enabled: bool) -> dict | None:
        """停用/启用单条（不删除，保留证据）；不存在返回 None"""
        with get_db() as session:
            session.execute(
                update(CategoryKeyword)
                .where(CategoryKeyword.id == keyword_id)
                .values(enabled=enabled)
            )
            row = session.get(CategoryKeyword, keyword_id)
            return row.as_dict() if row is not None else None

    @staticmethod
    def delete(keyword_id: int) -> bool:
        with get_db() as session:
            row = session.get(CategoryKeyword, keyword_id)
            if row is None:
                return False
            session.delete(row)
            return True

    @staticmethod
    def delete_by_category(session, category_id: int) -> int:
        """删除某分类的全部关键词（分类删除时的同事务级联清理）

        接受外部 session：必须与 CategoryDAO.delete 同事务，不能自建会话。
        """
        return session.execute(
            delete(CategoryKeyword).where(CategoryKeyword.category_id == category_id)
        ).rowcount
