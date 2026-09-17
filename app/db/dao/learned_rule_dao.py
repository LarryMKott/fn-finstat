"""分类自学习规则数据访问层（SQLAlchemy ORM，T-6.3）

规则全局共享（分类本身全局共用），(pattern, category) 唯一去重；
同一 pattern 的多行（用户改来改去的冲突场景）由服务层取 hits 最高者。
"""

import time

from sqlalchemy import select, update

from app.db.base import get_db
from app.db.models import LearnedRule


class LearnedRuleDAO:
    @staticmethod
    def list_all() -> list[dict]:
        """全部规则，按 hits 降序、更新时间降序（设置页规则列表）"""
        with get_db() as session:
            stmt = select(LearnedRule).order_by(
                LearnedRule.hits.desc(), LearnedRule.updated_at.desc()
            )
            return [r.as_dict() for r in session.scalars(stmt)]

    @staticmethod
    def list_enabled() -> list[dict]:
        """全部启用中的规则（导入归类匹配用，不做阈值过滤——阈值语义在服务层）"""
        with get_db() as session:
            stmt = select(LearnedRule).where(LearnedRule.enabled.is_(True))
            return [r.as_dict() for r in session.scalars(stmt)]

    @staticmethod
    def get(rule_id: int) -> dict | None:
        with get_db() as session:
            rule = session.scalar(select(LearnedRule).where(LearnedRule.id == rule_id))
            return rule.as_dict() if rule is not None else None

    @staticmethod
    def find(pattern: str, category: str) -> dict | None:
        """按 (pattern, category) 精确查找（纠正证据累加前判存）"""
        with get_db() as session:
            rule = session.scalar(
                select(LearnedRule).where(
                    LearnedRule.pattern == pattern, LearnedRule.category == category
                )
            )
            return rule.as_dict() if rule is not None else None

    @staticmethod
    def create(pattern: str, category: str) -> dict:
        """新建候选规则（hits=1，enabled=True）；并发唯一冲突由调用方处理"""
        now = time.time()
        with get_db() as session:
            rule = LearnedRule(
                pattern=pattern,
                category=category,
                hits=1,
                enabled=True,
                created_at=now,
                updated_at=now,
            )
            session.add(rule)
            session.flush()
            return rule.as_dict()

    @staticmethod
    def increment_hits(rule_id: int) -> int:
        """纠正证据累加（原子自增），返回累加后的 hits"""
        with get_db() as session:
            session.execute(
                update(LearnedRule)
                .where(LearnedRule.id == rule_id)
                .values(hits=LearnedRule.hits + 1, updated_at=time.time())
            )
            return session.scalar(
                select(LearnedRule.hits).where(LearnedRule.id == rule_id)
            )

    @staticmethod
    def re_enable(rule_id: int) -> None:
        """再次出现同一纠正方向时恢复启用（手动停用被新证据推翻）"""
        with get_db() as session:
            session.execute(
                update(LearnedRule)
                .where(LearnedRule.id == rule_id)
                .values(enabled=True, updated_at=time.time())
            )

    @staticmethod
    def update_fields(rule_id: int, fields: dict) -> dict | None:
        """按白名单字段更新（服务层校验后传入；fields 为空直接返回当前值）"""
        with get_db() as session:
            if fields:
                fields = {**fields, "updated_at": time.time()}
                session.execute(
                    update(LearnedRule)
                    .where(LearnedRule.id == rule_id)
                    .values(**fields)
                )
            rule = session.scalar(select(LearnedRule).where(LearnedRule.id == rule_id))
            return rule.as_dict() if rule is not None else None

    @staticmethod
    def delete(rule_id: int) -> bool:
        with get_db() as session:
            rule = session.scalar(select(LearnedRule).where(LearnedRule.id == rule_id))
            if rule is None:
                return False
            session.delete(rule)
            return True
