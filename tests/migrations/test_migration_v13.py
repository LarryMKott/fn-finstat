"""v12 → v13 schema 迁移测试：操作审计（audit_logs 纯新表）"""

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.config import DBSettings
from app.db.base import read_schema_version, set_schema_version
from app.db.dao.audit_dao import AuditDAO
from app.db.engine import _STATE
from app.db.migrations import _v13_add_audit_logs
from app.db.models import Base
from tests.conftest import make_engine


@pytest.fixture()
def v12_engine(tmp_path):
    engine = make_engine(tmp_path / "v12.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE audit_logs")
    with Session(engine) as session:
        set_schema_version(session, 12)
        session.commit()
    yield engine
    engine.dispose()


def _migrate(engine) -> None:
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 12
        _v13_add_audit_logs(session)
        set_schema_version(session, 13)
        session.commit()
    Base.metadata.create_all(engine)  # type: ignore[arg-type]
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 13


def test_v13_migration_is_idempotent(v12_engine):
    with Session(v12_engine) as session:
        _v13_add_audit_logs(session)
        set_schema_version(session, 13)
        session.commit()
        _v13_add_audit_logs(session)


def test_v13_migration_creates_audit_table(v12_engine):
    _migrate(v12_engine)
    assert "audit_logs" in inspect(v12_engine).get_table_names()


def test_v13_migration_then_dao_flow(v12_engine):
    _migrate(v12_engine)

    previous = _STATE.activate(DBSettings(db_type="sqlite"), v12_engine)
    if previous is not None:
        previous.dispose()

    AuditDAO.create(
        "10001", "bill.create", "bill", "1", "新增流水：X 10 元", 1700000000.0
    )
    total, rows = AuditDAO.list_logs("10001", None, 10, 0)
    assert total == 1
    assert rows[0]["action"] == "bill.create"
    # 他人查询不到
    assert AuditDAO.list_logs("10002", None, 10, 0)[0] == 0
    # 全量查询可见
    assert AuditDAO.list_logs(None, None, 10, 0)[0] == 1
