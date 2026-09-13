"""v2 → v3 schema 迁移测试：bills 补列（tags/reimbursed/deleted）与新表创建"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DBSettings
from app.db.base import _STATE, insert_ignore_rows, read_schema_version, set_schema_version
from app.db.migrations import _v3_add_tags_budget_assets
from app.db.dao.bill_dao import BillDAO
from app.db.models import Base, Bill, AssetSnapshot, Budget
from tests.conftest import USER_A, make_bill_records, make_engine


@pytest.fixture()
def v2_engine(tmp_path):
    """模拟 v2 老库：bills 缺新列、无 budgets/asset_snapshots 表，schema_version=2"""
    engine = make_engine(tmp_path / "v2.db")
    with engine.begin() as conn:
        conn.exec_driver_sql("""CREATE TABLE bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id VARCHAR(32) NOT NULL DEFAULT '',
                tx_time VARCHAR(32) NOT NULL,
                account VARCHAR(16) NOT NULL,
                tx_type VARCHAR(16) NOT NULL,
                merchant VARCHAR(256),
                amount FLOAT NOT NULL,
                category VARCHAR(64),
                tx_id VARCHAR(64) UNIQUE,
                remark VARCHAR(512)
            )""")
        conn.exec_driver_sql(
            "CREATE TABLE categories (id INTEGER PRIMARY KEY, name VARCHAR(64) UNIQUE)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE app_meta (meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL)"
        )
        conn.exec_driver_sql(
            "INSERT INTO bills (user_id, tx_time, account, tx_type, merchant, amount, "
            "category, tx_id, remark) "
            "VALUES ('10001', '2026-01-01 10:00:00', 'wechat', 'expense', '老数据', "
            "12.5, '餐饮', 'V2-1', '')"
        )
        conn.exec_driver_sql("INSERT INTO app_meta VALUES ('schema_version', '2')")
    yield engine
    engine.dispose()


def test_v3_migration_adds_columns(v2_engine):
    with Session(v2_engine) as session:
        assert read_schema_version(session) == 2
        _v3_add_tags_budget_assets(session)
        set_schema_version(session, 3)
        session.commit()

        # 新列存在且老数据拿到默认值
        bill = session.scalar(select(Bill).where(Bill.tx_id == "V2-1"))
        assert bill.tags == ""
        assert bill.reimbursed is False
        assert bill.deleted is False

        # 幂等：重复执行不报错
        _v3_add_tags_budget_assets(session)


def test_v3_migration_then_dao_flow(v2_engine):
    """迁移 + create_all 后激活引擎：核心插入（导入路径）依赖 DDL 默认值补齐新列"""
    with Session(v2_engine) as session:
        _v3_add_tags_budget_assets(session)
        set_schema_version(session, 3)
        session.commit()
    Base.metadata.create_all(v2_engine)  # 幂等补建 budgets / asset_snapshots

    previous = _STATE.activate(DBSettings(db_type="sqlite"), v2_engine)
    if previous is not None:
        previous.dispose()

    # budgets / asset_snapshots 表可用
    assert Budget.__table__.name in Base.metadata.tables
    with Session(v2_engine) as session:
        session.add(Budget(user_id=USER_A, month="2026-09", category="", amount=100))
        session.add(
            AssetSnapshot(
                user_id=USER_A,
                snap_date="2026-09-01",
                name="存款",
                asset_type="asset",
                amount=1,
            )
        )
        session.commit()

    # 核心插入路径（解析器导入路径）不含新列，依赖 DDL 默认值
    with v2_engine.begin() as conn:
        row = {**make_bill_records(1, prefix="V3M")[0], "user_id": USER_A}
        insert_ignore_rows(conn, Bill.__table__, [row])
    bill = BillDAO.list_bills(USER_A)[1][0]
    assert (
        bill["tags"] == "" and bill["reimbursed"] is False and bill["deleted"] is False
    )
