"""数据库基础设施测试：去重插入、schema 版本记录、唯一冲突转换、跨库搬移"""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import (
    LATEST_SCHEMA_VERSION, UniqueViolationError, as_unique_violation,
    insert_ignore_rows, set_schema_version, _get_schema_version,
)
from app.db.copy import copy_database
from app.db.models import Base, Bill, Category
from tests.conftest import USER_A, make_engine, make_bill_records


def test_insert_ignore_rows_skips_conflicts(db):
    from app.config import DEFAULT_CATEGORIES

    with db.begin() as conn:  # begin() 确保事务提交
        # 重复插入默认分类名：冲突行跳过
        inserted = insert_ignore_rows(
            conn, Category.__table__, [{"name": "餐饮"}, {"name": "品牌新分类"}]
        )
    assert inserted == 1
    with Session(db) as session:
        names = set(session.scalars(select(Category.name)))
    assert "品牌新分类" in names and names >= set(DEFAULT_CATEGORIES)


def test_insert_ignore_rows_empty(db):
    with db.connect() as conn:
        assert insert_ignore_rows(conn, Category.__table__, []) == 0


def test_unique_violation_error_wrapper():
    exc = IntegrityError("stmt", {}, Exception("UNIQUE constraint failed: bills.tx_id"))
    wrapped = as_unique_violation(exc)
    assert isinstance(wrapped, UniqueViolationError)
    assert "UNIQUE" in str(wrapped)


def test_plain_insert_duplicate_raises_integrity_error(db):
    payload = make_bill_records(1, tx_id="DUP-1")[0] | {"user_id": USER_A}
    with Session(db) as session:
        session.add(Bill(**payload))
        session.commit()
    with pytest.raises(IntegrityError):
        with Session(db) as session:
            session.add(Bill(**payload))
            session.commit()


def test_schema_version_roundtrip(db):
    with Session(db) as session:
        assert _get_schema_version(session) == LATEST_SCHEMA_VERSION  # 夹具预置为最新
        set_schema_version(session, 1)
        session.commit()
        assert _get_schema_version(session) == 1
        # 重复写覆盖旧值
        set_schema_version(session, LATEST_SCHEMA_VERSION)
        session.commit()
        assert _get_schema_version(session) == LATEST_SCHEMA_VERSION


def test_copy_database_empty_target_preserves_ids(tmp_path):
    source = make_engine(tmp_path / "src.db")
    target = make_engine(tmp_path / "dst.db")
    Base.metadata.create_all(source)
    Base.metadata.create_all(target)
    with Session(source) as session:
        session.add(Category(name="餐饮"))
        session.add(Category(name="交通"))
        session.flush()
        session.add(Bill(**(make_bill_records(2, category="餐饮")[0] | {"user_id": USER_A, "id": 1})))
        session.add(Bill(**(make_bill_records(2, category="餐饮")[1] | {"user_id": USER_A, "id": 2})))
        session.commit()

    stats = copy_database(source, target, LATEST_SCHEMA_VERSION)
    assert stats["source_bills"] == 2
    assert stats["copied_bills"] == 2
    assert stats["copied_categories"] == 2
    assert stats["target_had_data"] is False

    with Session(target) as session:
        assert session.scalar(select(Bill.id).order_by(Bill.id)) == 1  # 保留源 id
        assert session.scalar(
            select(Bill.user_id).where(Bill.id == 1)
        ) == USER_A  # 归属保留
        assert _get_schema_version(session) == LATEST_SCHEMA_VERSION
    source.dispose()
    target.dispose()


def test_copy_database_merges_nonempty_target_by_tx_id(tmp_path):
    source = make_engine(tmp_path / "src.db")
    target = make_engine(tmp_path / "dst.db")
    for engine in (source, target):
        Base.metadata.create_all(engine)

    with Session(target) as session:
        session.add(Category(name="购物"))
        session.add(Bill(**(make_bill_records(1, tx_id="SAME")[0] | {"user_id": USER_A})))
        session.commit()

    with Session(source) as session:
        session.add(Bill(**(make_bill_records(2, tx_id="SAME")[0] | {"user_id": ""})))
        session.add(Bill(**(make_bill_records(2, tx_id="NEW-1")[0] | {"user_id": ""})))
        session.commit()

    stats = copy_database(source, target, LATEST_SCHEMA_VERSION)
    assert stats["source_bills"] == 2
    assert stats["copied_bills"] == 1  # SAME 冲突跳过，NEW-1 新增
    assert stats["target_had_data"] is True

    with Session(target) as session:
        tx_ids = set(session.scalars(select(Bill.tx_id)))
        assert tx_ids == {"SAME", "NEW-1"}
        cat_names = set(session.scalars(select(Category.name)))
        assert "购物" in cat_names
        assert session.scalar(select(Bill.user_id).where(Bill.tx_id == "NEW-1")) == ""


def test_copy_database_from_legacy_v1_source_without_user_id(tmp_path):
    """源库是缺 user_id 列的旧版本时，按列交集搬移并归入默认账号"""
    source = make_engine(tmp_path / "legacy.db")
    target = make_engine(tmp_path / "dst.db")
    Base.metadata.create_all(target)
    with source.begin() as conn:
        # 手工建 v1 结构（无 user_id、无 app_meta）
        conn.exec_driver_sql(
            """CREATE TABLE bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_time VARCHAR(32) NOT NULL,
                account VARCHAR(16) NOT NULL,
                tx_type VARCHAR(16) NOT NULL,
                merchant VARCHAR(256),
                amount FLOAT NOT NULL,
                category VARCHAR(64),
                tx_id VARCHAR(64) UNIQUE,
                remark VARCHAR(512)
            )"""
        )
        conn.exec_driver_sql(
            "INSERT INTO bills (tx_time, account, tx_type, merchant, amount, category, tx_id, remark) "
            "VALUES ('2024-01-01 10:00:00', 'wechat', 'expense', '旧数据', 1.5, '餐饮', 'OLD-1', '')"
        )

    stats = copy_database(source, target, LATEST_SCHEMA_VERSION)
    assert stats["copied_bills"] == 1
    with Session(target) as session:
        legacy = session.scalar(select(Bill).where(Bill.tx_id == "OLD-1"))
        assert legacy.user_id == ""  # 缺失列补默认账号
        assert legacy.amount == 1.5
    source.dispose()
    target.dispose()
