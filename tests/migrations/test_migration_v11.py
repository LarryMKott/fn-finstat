"""v10 → v11 schema 迁移测试：报销工作流（reimbursements 新表 + bills.reimb_id）

本测试模拟「v10 老库（无报销表 / 无 reimb_id 列）→ v11」升级路径：
    1. 迁移幂等（重复执行不报错）
    2. 存量 reimbursed=1 的流水按账号归入「历史报销」（已结清），金额合计正确
    3. reimbursed=0 的流水不受影响
    4. 版本戳正确推进到 11
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.config import DBSettings
from app.db.base import read_schema_version, set_schema_version
from app.db.engine import _STATE
from app.db.dao.reimb_dao import ReimbDAO
from app.db.migrations import _v11_add_reimbursements
from app.db.models import Base
from tests.conftest import make_engine


# 与 v9/v10 迁移测试同策略：create_all（v11 形状）→ 拆出新增结构 → 回填 v10 数据
@pytest.fixture()
def v10_engine(tmp_path):
    engine = make_engine(tmp_path / "v10.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE reimbursements")
        conn.exec_driver_sql("DROP INDEX ix_bills_reimb_id")
        conn.exec_driver_sql("ALTER TABLE bills DROP COLUMN reimb_id")
        # v10 老数据：A 有两笔已报销（合计 90）+ 一笔未报销；B 一笔已报销
        rows = [
            ("10001", "2026-08-01 10:00:00", 40.0, "餐饮", 1, "MIG-A-1"),
            ("10001", "2026-08-02 10:00:00", 50.0, "交通", 1, "MIG-A-2"),
            ("10001", "2026-08-03 10:00:00", 60.0, "购物", 0, "MIG-A-3"),
            ("10002", "2026-08-04 10:00:00", 20.0, "餐饮", 1, "MIG-B-1"),
        ]
        for uid, tx_time, amount, category, reimbursed, tx_id in rows:
            conn.exec_driver_sql(
                "INSERT INTO bills (user_id, tx_time, account, tx_type, merchant, "
                "amount, category, tags, reimbursed, deleted, ledger_id, tx_id, remark) "
                f"VALUES ('{uid}', '{tx_time}', 'wechat', 'expense', '商户', "
                f"{amount}, '{category}', '', {reimbursed}, 0, 1, '{tx_id}', '')"
            )
    with Session(engine) as session:
        set_schema_version(session, 10)
        session.commit()
    yield engine
    engine.dispose()


def _migrate(engine) -> None:
    # init_db 顺序：create_all（补建 reimbursements 新表，已存在的 bills 不动）
    # 先于迁移（迁移只 ALTER bills + 回填数据）
    Base.metadata.create_all(engine)  # type: ignore[arg-type]
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 10
        _v11_add_reimbursements(session)
        set_schema_version(session, 11)
        session.commit()
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 11


def test_v11_migration_is_idempotent(v10_engine):
    Base.metadata.create_all(v10_engine)
    with Session(v10_engine) as session:
        _v11_add_reimbursements(session)
        set_schema_version(session, 11)
        session.commit()
        _v11_add_reimbursements(session)


def test_v11_migration_creates_structure(v10_engine):
    _migrate(v10_engine)
    inspector = inspect(v10_engine)
    assert "reimbursements" in inspector.get_table_names()
    cols = {c["name"] for c in inspector.get_columns("bills")}
    assert "reimb_id" in cols
    indexes = {i["name"] for i in inspector.get_indexes("bills")}
    assert "ix_bills_reimb_id" in indexes


def test_v11_migrates_legacy_reimbursed_bills(v10_engine):
    _migrate(v10_engine)

    previous = _STATE.activate(DBSettings(db_type="sqlite"), v10_engine)
    if previous is not None:
        previous.dispose()

    # A 的「历史报销」：两笔已报销流水合计 90，状态已结清
    claims = ReimbDAO.list_with_stats("10001")
    assert len(claims) == 1
    claim = claims[0]
    assert claim["title"] == "历史报销"
    assert claim["status"] == "settled"
    assert claim["bill_count"] == 2
    assert claim["total_amount"] == 90

    # 已报销流水都挂到了迁移出的报销单上；未报销流水不受影响
    with Session(v10_engine) as session:
        row = session.execute(
            text(
                "SELECT COUNT(*) FROM bills WHERE user_id='10001' "
                "AND reimbursed=1 AND reimb_id IS NOT NULL"
            )
        ).scalar()
        plain = session.execute(
            text(
                "SELECT COUNT(*) FROM bills WHERE user_id='10001' "
                "AND reimbursed=0 AND reimb_id IS NULL"
            )
        ).scalar()
    assert row == 2
    assert plain == 1

    # 账号隔离：B 有自己的历史报销，互不混淆
    claims_b = ReimbDAO.list_with_stats("10002")
    assert len(claims_b) == 1
    assert claims_b[0]["total_amount"] == 20
    assert claims_b[0]["id"] != claim["id"]
