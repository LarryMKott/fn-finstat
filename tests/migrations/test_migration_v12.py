"""v11 → v12 schema 迁移测试：借贷台账新表（loans / loan_payments）

v12 与 v4~v7/v9 同为纯新表模式：create_all 幂等建表，迁移只推进版本戳。
本测试模拟「v11 老库（无借贷表）→ v12」升级路径：
    1. 迁移幂等（重复执行不报错）
    2. 两张新表存在
    3. 迁移完成后 DAO 全流程可用（建借贷 / 登记还款 / 进度推导）
    4. 版本戳正确推进到 12
"""

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.config import DBSettings
from app.db.base import read_schema_version, set_schema_version
from app.db.dao.loan_dao import LoanDAO
from app.db.engine import _STATE
from app.db.migrations import _v12_add_loan_tables
from app.db.models import Base
from tests.conftest import make_engine


@pytest.fixture()
def v11_engine(tmp_path):
    """模拟 v11 老库：借贷表不存在，schema_version=11"""
    engine = make_engine(tmp_path / "v11.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE loan_payments")
        conn.exec_driver_sql("DROP TABLE loans")
    with Session(engine) as session:
        set_schema_version(session, 11)
        session.commit()
    yield engine
    engine.dispose()


def _migrate(engine) -> None:
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 11
        _v12_add_loan_tables(session)
        set_schema_version(session, 12)
        session.commit()
    Base.metadata.create_all(engine)  # type: ignore[arg-type]
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 12


def test_v12_migration_is_idempotent(v11_engine):
    with Session(v11_engine) as session:
        _v12_add_loan_tables(session)
        set_schema_version(session, 12)
        session.commit()
        _v12_add_loan_tables(session)


def test_v12_migration_creates_loan_tables(v11_engine):
    _migrate(v11_engine)
    tables = set(inspect(v11_engine).get_table_names())
    assert {"loans", "loan_payments"} <= tables


def test_v12_migration_then_dao_flow(v11_engine):
    """迁移 + create_all 后激活引擎：借贷 DAO 全流程与「还清即结项」推导"""
    _migrate(v11_engine)

    previous = _STATE.activate(DBSettings(db_type="sqlite"), v11_engine)
    if previous is not None:
        previous.dispose()

    loan = LoanDAO.create(
        "10001",
        {
            "direction": "lend",
            "counterparty": "老王",
            "principal": 500.0,
            "loan_date": "2026-09-01",
            "due_date": "2026-10-01",
            "note": "",
            "status": "open",
        },
    )
    LoanDAO.add_payment(
        loan["id"], {"amount": 200.0, "pay_date": "2026-09-15", "note": "第一笔"}
    )
    assert LoanDAO.repaid_total(loan["id"], "10001") == 200

    LoanDAO.add_payment(
        loan["id"], {"amount": 300.0, "pay_date": "2026-09-20", "note": "结清"}
    )
    assert LoanDAO.repaid_total(loan["id"], "10001") == 500
    assert len(LoanDAO.list_payments(loan["id"])) == 2

    # list_payments 按还款日期倒序：首行是 300 的那笔，删后剩 200
    assert LoanDAO.delete_payment(
        LoanDAO.list_payments(loan["id"])[0]["id"], loan["id"]
    )
    assert LoanDAO.repaid_total(loan["id"], "10001") == 200

    assert LoanDAO.delete(loan["id"], "10001") is True
    assert LoanDAO.get(loan["id"], "10001") is None
