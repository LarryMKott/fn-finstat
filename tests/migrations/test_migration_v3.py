"""v2 → v3 schema 迁移测试：bills 补列（tags/reimbursed/deleted）与新表创建"""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import DBSettings
from app.db.engine import _STATE

from app.db.base import (
    insert_ignore_rows,
    read_schema_version,
    set_schema_version,
)
from app.db.migrations import _v3_add_tags_budget_assets
from app.db.dao.bill_dao import BillDAO
from app.db.models import Base, Bill, AssetSnapshot, Budget
from tests.conftest import USER_A, make_bill_records, make_engine


@pytest.fixture()
def v2_engine(tmp_path):
    """模拟 v2 老库：bills 缺 v3 三列、无 budgets/asset_snapshots 表，schema_version=2

    构造方式与 v5/v6/v7 的迁移测试一致——先按当前模型建表，再「回退」掉被测版本
    引入的改动，而不是手写老版本 DDL。原因：ORM 模型恒为最新 schema，凡经 ORM 或
    核心表（`Bill.__table__`）访问的用例都要求列集一致；若手写 v2 DDL，bills 会
    缺少后续版本（如 v8 的 ledger_id）的列，任何 ORM 查询都会报 no such column。
    本文件验证的是 v3 补的那三列，其余列保留当前基线不影响结论。
    """
    engine = make_engine(tmp_path / "v2.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE bills DROP COLUMN tags")
        conn.exec_driver_sql("ALTER TABLE bills DROP COLUMN reimbursed")
        conn.exec_driver_sql("ALTER TABLE bills DROP COLUMN deleted")
        conn.exec_driver_sql("DROP TABLE budgets")
        conn.exec_driver_sql("DROP TABLE asset_snapshots")
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
