"""v13 → v14 schema 迁移测试：储蓄目标新表（savings_goals）"""

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.config import DBSettings
from app.db.base import read_schema_version, set_schema_version
from app.db.dao.savings_dao import SavingsGoalDAO
from app.db.engine import _STATE
from app.db.migrations import _v14_add_savings_goals
from app.db.models import Base
from tests.conftest import make_engine


@pytest.fixture()
def v13_engine(tmp_path):
    engine = make_engine(tmp_path / "v13.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE savings_goals")
    with Session(engine) as session:
        set_schema_version(session, 13)
        session.commit()
    yield engine
    engine.dispose()


def _migrate(engine) -> None:
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 13
        _v14_add_savings_goals(session)
        set_schema_version(session, 14)
        session.commit()
    Base.metadata.create_all(engine)  # type: ignore[arg-type]
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 14


def test_v14_migration_is_idempotent(v13_engine):
    with Session(v13_engine) as session:
        _v14_add_savings_goals(session)
        set_schema_version(session, 14)
        session.commit()
        _v14_add_savings_goals(session)


def test_v14_migration_creates_table(v13_engine):
    _migrate(v13_engine)
    assert "savings_goals" in inspect(v13_engine).get_table_names()


def test_v14_migration_then_dao_flow(v13_engine):
    _migrate(v13_engine)

    previous = _STATE.activate(DBSettings(db_type="sqlite"), v13_engine)
    if previous is not None:
        previous.dispose()

    goal = SavingsGoalDAO.create(
        "10001",
        {
            "name": "应急金",
            "target_amount": 30000.0,
            "start_date": "2026-09-01",
            "target_date": "2026-12-31",
            "note": "",
        },
    )
    assert SavingsGoalDAO.get(goal["id"], "10001")["target_amount"] == 30000
    assert SavingsGoalDAO.delete(goal["id"], "10001") is True
    assert SavingsGoalDAO.get(goal["id"], "10001") is None
