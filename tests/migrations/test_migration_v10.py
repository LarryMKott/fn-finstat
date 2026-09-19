"""v9 → v10 schema 迁移测试：家庭预算（budgets.family_id 可空列，T-7.3）

本测试模拟「v9 老库（budgets 无 family_id 列）→ v10」升级路径：
    1. 列迁移幂等（重复执行不报错）
    2. 升级前已存在的个人预算行原样保留（family_id 为 NULL）
    3. 迁移后家庭预算 DAO 全流程可用（合成属主 + family_id 落列）
    4. 版本戳正确推进到 10
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.config import DBSettings
from app.db.base import read_schema_version, set_schema_version
from app.db.dao.budget_dao import BudgetDAO
from app.db.engine import _STATE
from app.db.migrations import _v10_add_family_budget
from app.db.models import Base
from tests.conftest import make_engine


@pytest.fixture()
def v9_engine(tmp_path):
    """模拟 v9 老库：budgets 无 family_id 列，且已有一行个人预算，schema_version=9"""
    engine = make_engine(tmp_path / "v9.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX ix_budgets_family_id")
        conn.exec_driver_sql("ALTER TABLE budgets DROP COLUMN family_id")
        # 升级前已存在的个人预算行（列不存在，按 v9 形状裸插）
        conn.exec_driver_sql(
            "INSERT INTO budgets (user_id, ledger_id, month, category, amount) "
            "VALUES ('10001', 1, '2026-09', '餐饮', 100)"
        )
    with Session(engine) as session:
        set_schema_version(session, 9)
        session.commit()
    yield engine
    engine.dispose()


def _migrate(engine) -> None:
    """跑迁移 + create_all（复刻 init_db 的升级路径），断言版本推进到 10"""
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 9
        _v10_add_family_budget(session)
        set_schema_version(session, 10)
        session.commit()
    Base.metadata.create_all(engine)  # type: ignore[arg-type]
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 10


def test_v10_migration_is_idempotent(v9_engine):
    with Session(v9_engine) as session:
        _v10_add_family_budget(session)
        session.commit()
        _v10_add_family_budget(session)


def test_v10_migration_adds_column_and_keeps_personal_rows(v9_engine):
    _migrate(v9_engine)

    cols = {c["name"] for c in inspect(v9_engine).get_columns("budgets")}
    assert "family_id" in cols
    indexes = {i["name"] for i in inspect(v9_engine).get_indexes("budgets")}
    assert "ix_budgets_family_id" in indexes

    # 升级前的个人预算行原样保留，family_id 为 NULL
    with Session(v9_engine) as session:
        row = session.execute(
            text(
                "SELECT user_id, family_id FROM budgets "
                "WHERE month='2026-09' AND category='餐饮'"
            )
        ).first()
    assert row is not None
    assert row[0] == "10001" and row[1] is None


def test_v10_migration_then_family_budget_flow(v9_engine):
    """迁移 + create_all 后激活引擎：个人预算不受影响，家庭预算可落库"""
    _migrate(v9_engine)

    previous = _STATE.activate(DBSettings(db_type="sqlite"), v9_engine)
    if previous is not None:
        previous.dispose()

    personal = BudgetDAO.upsert("10001", "2026-09", "交通", 80, 1)
    assert personal["family_id"] is None

    family_budget = BudgetDAO.upsert_family(7, "10001", "2026-09", "餐饮", 300)
    assert family_budget["family_id"] == 7
    assert family_budget["user_id"] == "family:7"

    # 同月同分类：个人预算与家庭预算共存（合成属主避开唯一键冲突）
    assert len(BudgetDAO.list_month("10001", "2026-09", 1)) == 2
    assert [b["amount"] for b in BudgetDAO.list_family(7, "2026-09")] == [300]
    assert BudgetDAO.delete_in_family(family_budget["id"], 8) is False  # 异家庭不可删
    assert BudgetDAO.delete_in_family(family_budget["id"], 7) is True
