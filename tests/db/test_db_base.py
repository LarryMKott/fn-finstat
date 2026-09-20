"""数据库基础设施测试：去重插入、schema 版本记录、唯一冲突转换、跨库搬移"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.engine import is_unique_violation
from app.db.base import (
    LATEST_SCHEMA_VERSION,
    insert_ignore_rows,
    read_schema_version,
    set_schema_version,
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


def test_unique_violation_detection():
    exc = IntegrityError("stmt", {}, Exception("UNIQUE constraint failed: bills.tx_id"))
    assert is_unique_violation(exc) is True
    assert not is_unique_violation(
        IntegrityError("stmt", {}, Exception("FOREIGN KEY constraint failed"))
    )


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
        assert read_schema_version(session) == LATEST_SCHEMA_VERSION  # 夹具预置为最新
        set_schema_version(session, 1)
        session.commit()
        assert read_schema_version(session) == 1
        # 重复写覆盖旧值
        set_schema_version(session, LATEST_SCHEMA_VERSION)
        session.commit()
        assert read_schema_version(session) == LATEST_SCHEMA_VERSION


def test_copy_database_empty_target_preserves_ids(tmp_path):
    source = make_engine(tmp_path / "src.db")
    target = make_engine(tmp_path / "dst.db")
    Base.metadata.create_all(source)
    Base.metadata.create_all(target)
    with Session(source) as session:
        session.add(Category(name="餐饮"))
        session.add(Category(name="交通"))
        session.flush()
        session.add(
            Bill(
                **(
                    make_bill_records(2, category="餐饮")[0]
                    | {"user_id": USER_A, "id": 1}
                )
            )
        )
        session.add(
            Bill(
                **(
                    make_bill_records(2, category="餐饮")[1]
                    | {"user_id": USER_A, "id": 2}
                )
            )
        )
        session.commit()

    stats = copy_database(source, target, LATEST_SCHEMA_VERSION)
    assert stats["source_bills"] == 2
    assert stats["copied_bills"] == 2
    assert stats["copied_categories"] == 2
    assert stats["target_had_data"] is False

    with Session(target) as session:
        assert session.scalar(select(Bill.id).order_by(Bill.id)) == 1  # 保留源 id
        assert (
            session.scalar(select(Bill.user_id).where(Bill.id == 1)) == USER_A
        )  # 归属保留
        assert read_schema_version(session) == LATEST_SCHEMA_VERSION
    source.dispose()
    target.dispose()


def test_copy_database_merges_nonempty_target_by_tx_id(tmp_path):
    source = make_engine(tmp_path / "src.db")
    target = make_engine(tmp_path / "dst.db")
    for engine in (source, target):
        Base.metadata.create_all(engine)

    with Session(target) as session:
        session.add(Category(name="购物"))
        session.add(
            Bill(**(make_bill_records(1, tx_id="SAME")[0] | {"user_id": USER_A}))
        )
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
        conn.exec_driver_sql("""CREATE TABLE bills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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


def test_copy_database_merge_does_not_duplicate_null_tx_ids(tmp_path):
    """合并模式重复搬移：无交易号流水按业务键跳过，不再每次翻倍（评审 H-4）

    tx_id 唯一约束不去重 NULL（SQL 语义 NULL ≠ NULL）。业务键无法绝对精确，
    单次搬移内业务键相同的合法重复行全部保留，仅防跨次重复膨胀。
    """
    source = make_engine(tmp_path / "src.db")
    target = make_engine(tmp_path / "dst.db")
    for engine in (source, target):
        Base.metadata.create_all(engine)
    with Session(target) as session:
        session.add(Category(name="餐饮"))
        session.commit()

    with Session(source) as session:
        for r in make_bill_records(2, tx_id=None, merchant="手工记账", category="餐饮"):
            session.add(Bill(**(r | {"user_id": USER_A})))
        session.add(
            Bill(
                **(
                    make_bill_records(
                        1, tx_id=None, merchant="另一商户", category="餐饮"
                    )[0]
                    | {"user_id": USER_A}
                )
            )
        )
        session.commit()

    first = copy_database(source, target, LATEST_SCHEMA_VERSION)
    assert first["copied_bills"] == 3  # 单次搬移内的合法重复行全保留

    second = copy_database(source, target, LATEST_SCHEMA_VERSION)
    assert second["copied_bills"] == 0  # 重复搬移不再翻倍

    with Session(target) as session:
        assert session.scalar(select(func.count()).select_from(Bill)) == 3
        assert (
            session.scalar(
                select(func.count()).select_from(Bill).where(Bill.tx_id.is_(None))
            )
            == 3
        )
    source.dispose()
    target.dispose()


def _seed_new_module_source(session: Session) -> None:
    """源库造四张新业务表数据：借条+还款、报销单+挂单流水、储蓄目标"""
    from app.db.models import Loan, LoanPayment, Reimbursement, SavingsGoal

    session.add(
        Loan(
            id=1,
            user_id=USER_A,
            direction="lend",
            counterparty="老王",
            principal=1000,
            loan_date="2026-02-01",
        )
    )
    session.add(LoanPayment(id=1, loan_id=1, amount=400, pay_date="2026-03-01"))
    session.add(Reimbursement(id=9, user_id=USER_A, title="出差报销", created_at=100))
    session.flush()
    session.add(
        Bill(
            **(
                make_bill_records(1, prefix="RB")[0]
                | {"user_id": USER_A, "id": 10, "reimb_id": 9, "reimbursed": True}
            )
        )
    )
    session.add(
        SavingsGoal(
            id=1,
            user_id=USER_A,
            name="应急金",
            target_amount=20000,
            start_date="2026-01-01",
        )
    )
    session.commit()


def test_copy_database_moves_new_module_tables(tmp_path):
    """整库搬移：借贷/还款/报销/储蓄目标随数据走，reimb_id 引用保持完整（P0）"""
    source = make_engine(tmp_path / "src.db")
    target = make_engine(tmp_path / "dst.db")
    for engine in (source, target):
        Base.metadata.create_all(engine)
    with Session(source) as session:
        _seed_new_module_source(session)

    stats = copy_database(source, target, LATEST_SCHEMA_VERSION)
    assert stats["copied_loans"] == 1
    assert stats["copied_loan_payments"] == 1
    assert stats["copied_reimbursements"] == 1
    assert stats["copied_savings_goals"] == 1

    from app.db.models import Loan, LoanPayment, Reimbursement, SavingsGoal

    with Session(target) as session:
        assert session.scalar(select(func.count()).select_from(Loan)) == 1
        payment = session.scalar(select(LoanPayment))
        assert payment is not None and payment.loan_id == 1  # 还款仍挂在借条上
        bill = session.scalar(select(Bill).where(Bill.id == 10))
        assert bill is not None and bill.reimb_id == 9  # 挂单流水引用不悬挂
        assert session.scalar(select(func.count()).select_from(Reimbursement)) == 1
        assert session.scalar(select(func.count()).select_from(SavingsGoal)) == 1
    source.dispose()
    target.dispose()


def test_copy_database_merge_remaps_reimb_and_family_budget(tmp_path):
    """合并搬移：bills.reimb_id 按业务键重映射到目标库新报销单，家庭预算行
    family_id/user_id 按邀请码改写；重复搬移四表不再翻倍"""
    from app.core.constants import family_scope_user
    from app.db.models import Budget, Family, Loan, Reimbursement, SavingsGoal

    source = make_engine(tmp_path / "src.db")
    target = make_engine(tmp_path / "dst.db")
    for engine in (source, target):
        Base.metadata.create_all(engine)

    # 目标库先有无关数据（非空目标 → 合并模式）+ 同名邀请码的其他家庭
    with Session(target) as session:
        session.add(Category(name="餐饮"))
        session.add(Family(id=1, name="别家", invite_code="OTHERCOD"))
        session.add(Reimbursement(id=5, user_id=USER_A, title="旧报销", created_at=1))
        session.commit()

    with Session(source) as session:
        _seed_new_module_source(session)
        session.add(
            Family(id=1, name="老家", invite_code="FAMCODE1")
        )  # 源家庭 id 与目标库已有家庭撞 id
        session.add(
            Budget(
                user_id=family_scope_user(1),
                family_id=1,
                month="2026-09",
                category="餐饮",
                amount=500,
            )
        )
        session.commit()

    stats = copy_database(source, target, LATEST_SCHEMA_VERSION)
    assert stats["copied_loans"] == 1 and stats["copied_reimbursements"] == 1

    with Session(target) as session:
        claim = session.scalar(
            select(Reimbursement).where(Reimbursement.title == "出差报销")
        )
        assert claim is not None
        # 合并模式流水 id 由目标库重新编号，按业务键 tx_id 查
        bill = session.scalar(select(Bill).where(Bill.tx_id == "RB-0000"))
        # 报销单 id 重编号后（目标库已有 id=5 的报销），流水引用跟着改写
        assert bill is not None and bill.reimb_id == claim.id and claim.id != 9
        # 家庭预算挂到目标库「老家」的新 id 上，而不是撞 id 的「别家」
        src_family = session.scalar(
            select(Family).where(Family.invite_code == "FAMCODE1")
        )
        budget = session.scalar(
            select(Budget).where(Budget.month == "2026-09", Budget.category == "餐饮")
        )
        assert budget is not None
        assert budget.family_id == src_family.id
        assert budget.user_id == family_scope_user(src_family.id)

    # 重复搬移：借条/还款/报销/储蓄按业务键去重，不再翻倍
    second = copy_database(source, target, LATEST_SCHEMA_VERSION)
    assert second["copied_loans"] == 0
    assert second["copied_loan_payments"] == 0
    assert second["copied_reimbursements"] == 0
    assert second["copied_savings_goals"] == 0
    with Session(target) as session:
        assert session.scalar(select(func.count()).select_from(Loan)) == 1
        assert session.scalar(select(func.count()).select_from(SavingsGoal)) == 1
    source.dispose()
    target.dispose()
