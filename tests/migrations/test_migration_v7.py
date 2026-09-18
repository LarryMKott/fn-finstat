"""v6 → v7 schema 迁移测试：分类自学习新表（learned_rules）

v7 迁移策略与 v4/v5/v6 一致：新表由 init_db 的 Base.metadata.create_all 按
方言幂等创建（(pattern, category) 唯一约束做纠正证据去重），迁移函数本身只
推进版本戳。本测试模拟「v6 老库（无学习规则表）→ v7」的升级路径，验证：
    1. 迁移函数幂等（重复执行不报错）
    2. create_all 在迁移后补建 learned_rules 表
    3. (pattern, category) 唯一约束生效
    4. 迁移完成后 DAO 流程可用（建候选行 / hits 累加 / 编辑 / 删除）
    5. 版本戳正确推进到 7
"""

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import DBSettings
from app.db.base import read_schema_version, set_schema_version
from app.db.dao.learned_rule_dao import LearnedRuleDAO
from app.db.engine import _STATE
from app.db.migrations import _v7_add_learned_rules_table
from app.db.models import Base, LearnedRule
from tests.conftest import make_engine


@pytest.fixture()
def v6_engine(tmp_path):
    """模拟 v6 老库：v6 表已建好，但 learned_rules 不存在，schema_version=6"""
    engine = make_engine(tmp_path / "v6.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE learned_rules")
    with Session(engine) as session:
        set_schema_version(session, 6)
        session.commit()
    yield engine
    engine.dispose()


def _migrate(engine: object) -> None:
    """跑迁移 + create_all 补建新表（复刻 init_db 的升级路径）"""
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 6
        _v7_add_learned_rules_table(session)
        set_schema_version(session, 7)
        session.commit()
    Base.metadata.create_all(engine)  # type: ignore[arg-type]
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 7


def test_v7_migration_is_idempotent(v6_engine):
    """迁移函数本身幂等（无列级变更，仅推进版本戳，可重复执行）"""
    with Session(v6_engine) as session:
        _v7_add_learned_rules_table(session)
        set_schema_version(session, 7)
        session.commit()
        _v7_add_learned_rules_table(session)


def test_v7_migration_creates_learned_rules_table(v6_engine):
    _migrate(v6_engine)

    inspector = inspect(v6_engine)
    assert "learned_rules" in set(inspector.get_table_names())
    cols = {c["name"] for c in inspector.get_columns("learned_rules")}
    assert {
        "id",
        "pattern",
        "category",
        "hits",
        "enabled",
        "created_at",
        "updated_at",
    } <= cols
    uniques = inspector.get_unique_constraints("learned_rules")
    assert any(set(u["column_names"]) == {"pattern", "category"} for u in uniques)


def test_v7_migration_pattern_category_unique_enforced(v6_engine):
    _migrate(v6_engine)

    with Session(v6_engine) as session:
        session.add(
            LearnedRule(
                pattern="瑞幸", category="餐饮", hits=1, created_at=1.0, updated_at=1.0
            )
        )
        session.commit()

    with pytest.raises(IntegrityError):
        with Session(v6_engine) as session:
            session.add(
                LearnedRule(
                    pattern="瑞幸",
                    category="餐饮",
                    hits=5,
                    created_at=2.0,
                    updated_at=2.0,
                )
            )
            session.commit()


def test_v7_migration_then_dao_flow(v6_engine):
    """迁移 + create_all 后激活引擎：学习规则 DAO 全流程可用"""
    _migrate(v6_engine)

    previous = _STATE.activate(DBSettings(db_type="sqlite"), v6_engine)
    if previous is not None:
        previous.dispose()

    # 建候选行 → 同方向累加 → 唯一查重
    rule = LearnedRuleDAO.create("瑞幸", "餐饮")
    assert rule["hits"] == 1
    assert LearnedRuleDAO.find("瑞幸", "餐饮")["id"] == rule["id"]
    assert LearnedRuleDAO.increment_hits(rule["id"]) == 2

    # 编辑与删除
    updated = LearnedRuleDAO.update_fields(rule["id"], {"enabled": False})
    assert updated["enabled"] is False
    assert LearnedRuleDAO.delete(rule["id"]) is True
    assert LearnedRuleDAO.get(rule["id"]) is None
