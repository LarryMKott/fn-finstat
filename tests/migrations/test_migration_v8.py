"""v7 → v8 schema 迁移测试：账本维度（T-7.1）

v8 是引入账本维度的第一个版本，也是 v0.7 唯一一次破坏性 schema 变更，因此本
文件按开发计划的验收标准逐条覆盖：

    1. 迁移后默认账本存在，历史数据（流水/预算/资产快照）全部挂在它上面
    2. 历史数据 0 丢失（条数与金额逐项核对）
    3. 不传 ledger_id 的旧调用仍落在默认账本（行为与升级前完全等价）
    4. budgets 唯一键升级为含 ledger_id（SQLite 实测重建 + MySQL/PG 走 SQL 断言）
    5. 迁移函数幂等（重复执行不报错、数据不变）

SQLite 实测覆盖真实升级路径；MySQL / PostgreSQL 无法在单测里起实例，按
「SQL 生成断言」保证三方言语义对齐（见 _budget_unique_upgrade_sql）。
"""

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import DEFAULT_LEDGER_NAME, DBSettings
from app.db.base import read_schema_version, set_schema_version
from app.db.dao.bill_dao import BillDAO
from app.db.dao.budget_dao import BudgetDAO
from app.db.dao.ledger_dao import LedgerDAO
from app.db.engine import _STATE
from app.db.migrations import (
    _budget_unique_ledger_check_sql,
    _budget_unique_upgrade_sql,
    _v8_add_ledger_dimension,
)
from app.db.models import AssetSnapshot, Base, Bill, Budget, Ledger
from tests.conftest import USER_A, make_engine

USER_B = "10002"

# v7 形态的 budgets：唯一键不含 ledger_id（升级前口径）
_V7_BUDGETS_DDL = """CREATE TABLE budgets (
    id       INTEGER NOT NULL,
    user_id  VARCHAR(32) NOT NULL,
    month    VARCHAR(7) NOT NULL,
    category VARCHAR(64) NOT NULL,
    amount   FLOAT NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_budget_scope UNIQUE (user_id, month, category)
)"""


@pytest.fixture()
def v7_engine(tmp_path):
    """模拟 v7 老库：无 ledgers 表、三张业务表无 ledger_id 列、budgets 唯一键不含账本

    构造方式是在最新版建表结果上「回退」掉 v8 的改动，保证除账本维度外其余
    表结构与真实 v7 库一致（v7 的其他表由 create_all 原样保留）。
    """
    engine = make_engine(tmp_path / "v7.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for index in (
            "ix_bills_ledger_id",
            "ix_budgets_ledger_id",
            "ix_asset_snapshots_ledger_id",
        ):
            conn.exec_driver_sql(f"DROP INDEX IF EXISTS {index}")
        conn.exec_driver_sql("ALTER TABLE bills DROP COLUMN ledger_id")
        conn.exec_driver_sql("ALTER TABLE asset_snapshots DROP COLUMN ledger_id")
        conn.exec_driver_sql("DROP TABLE ledgers")
        conn.exec_driver_sql("DROP TABLE budgets")
        conn.exec_driver_sql(_V7_BUDGETS_DDL)
        # 历史数据：2 条流水（两个账号）+ 1 条预算 + 1 条资产快照
        conn.execute(
            text(
                "INSERT INTO bills (user_id, tx_time, account, tx_type, merchant,"
                " amount, category, tx_id, remark, tags, reimbursed, deleted)"
                " VALUES (:u,'2026-01-01 10:00:00','wechat','expense','老商户',"
                "12.5,'餐饮','OLD-1','','',0,0),"
                "(:u2,'2026-02-02 10:00:00','alipay','income','老收入',"
                "200.0,'其他','OLD-2','','',0,0)"
            ),
            {"u": USER_A, "u2": USER_B},
        )
        conn.execute(
            text(
                "INSERT INTO budgets (user_id, month, category, amount)"
                " VALUES (:u,'2026-01','餐饮',500)"
            ),
            {"u": USER_A},
        )
        conn.execute(
            text(
                "INSERT INTO asset_snapshots (user_id, snap_date, name, asset_type,"
                " amount, remark) VALUES (:u,'2026-01-01','招商','asset',1000,'')"
            ),
            {"u": USER_A},
        )
    with Session(engine) as session:
        set_schema_version(session, 7)
        session.commit()
    yield engine
    engine.dispose()


def _migrate(engine) -> None:
    """复刻 init_db 的升级路径：先 create_all 补建新表（ledgers），再跑迁移

    顺序不能反：init_db 是「建表 → 逐版本迁移」，ledgers 表因此由 create_all
    建好，迁移函数只负责在其中建默认账本行。
    """
    Base.metadata.create_all(engine)  # type: ignore[arg-type]
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 7
        _v8_add_ledger_dimension(session)
        set_schema_version(session, 8)
        session.commit()
    Base.metadata.create_all(engine)  # type: ignore[arg-type]
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 8


def _activate(engine) -> None:
    previous = _STATE.activate(DBSettings(db_type="sqlite"), engine)
    if previous is not None:
        previous.dispose()


def test_v8_migration_creates_default_ledger(v7_engine):
    _migrate(v7_engine)

    with Session(v7_engine) as session:
        ledgers = list(session.scalars(select(Ledger).order_by(Ledger.id)))
    assert len(ledgers) == 1
    assert ledgers[0].name == DEFAULT_LEDGER_NAME
    assert ledgers[0].is_default is True
    assert ledgers[0].owner_id == ""


def test_v8_migration_backfills_history_without_loss(v7_engine):
    """历史流水 / 预算 / 资产快照 0 丢失：条数与金额与迁移前逐项一致"""
    _migrate(v7_engine)

    with Session(v7_engine) as session:
        default_id = session.scalar(
            select(Ledger.id).where(Ledger.is_default.is_(True))
        )
        bills = list(session.scalars(select(Bill).order_by(Bill.id)))
        budgets = list(session.scalars(select(Budget)))
        assets = list(session.scalars(select(AssetSnapshot)))

    assert default_id is not None
    assert [(b.merchant, b.amount, b.user_id) for b in bills] == [
        ("老商户", 12.5, USER_A),
        ("老收入", 200.0, USER_B),
    ]
    assert all(b.ledger_id == default_id for b in bills)
    assert [(b.month, b.category, b.amount) for b in budgets] == [
        ("2026-01", "餐饮", 500.0)
    ]
    assert budgets[0].ledger_id == default_id
    assert [(a.name, a.amount) for a in assets] == [("招商", 1000.0)]
    assert assets[0].ledger_id == default_id


def test_v8_migration_is_idempotent(v7_engine):
    """迁移函数可重复执行：列/索引/唯一键的变更全部按存在性判断后跳过"""
    Base.metadata.create_all(v7_engine)  # 复刻 init_db：ledgers 表先由 create_all 建好
    with Session(v7_engine) as session:
        _v8_add_ledger_dimension(session)
        set_schema_version(session, 8)
        session.commit()
        first = [
            (b.id, b.ledger_id) for b in session.scalars(select(Bill).order_by(Bill.id))
        ]
        _v8_add_ledger_dimension(session)
        session.commit()
        second = [
            (b.id, b.ledger_id) for b in session.scalars(select(Bill).order_by(Bill.id))
        ]
    assert first == second == [(1, 1), (2, 1)]


def test_v8_migration_old_calls_still_land_on_default_ledger(v7_engine):
    """不传 ledger_id 的旧调用行为不变：写入后仍在默认账本，读取口径也不变"""
    _migrate(v7_engine)
    _activate(v7_engine)

    default_id = LedgerDAO.default_id()
    bill_id = BillDAO.create(
        {
            "tx_time": "2026-03-03 10:00:00",
            "account": "wechat",
            "tx_type": "expense",
            "merchant": "新商户",
            "amount": 8.8,
            "category": "餐饮",
            "tx_id": "NEW-1",
        },
        USER_A,
    )
    bill = BillDAO.get_by_id(bill_id, USER_A)
    assert bill["ledger_id"] == default_id

    # 读路径不传 ledger_id → 不按账本过滤（升级前只有默认账本，语义等价）
    total, rows = BillDAO.list_bills(USER_A, page_size=50)
    assert total == 2 and {r["tx_id"] for r in rows} == {"OLD-1", "NEW-1"}
    # 传了 ledger_id → 按账本过滤（此时全库只有默认账本，条数一致）
    total, _ = BillDAO.list_bills(USER_A, page_size=50, ledger_id=default_id)
    assert total == 2


def test_v8_migration_budget_unique_includes_ledger(v7_engine):
    """唯一键升级后：同账号同月同分类在不同账本下可各存一份预算"""
    _migrate(v7_engine)
    _activate(v7_engine)

    BudgetDAO.upsert(USER_A, "2026-01", "餐饮", 600.0)  # 默认账本，覆盖历史值
    constraints = [
        u
        for u in inspect(v7_engine).get_unique_constraints("budgets")
        if u["name"] == "uq_budget_scope"
    ]
    assert constraints and "ledger_id" in constraints[0]["column_names"]

    other = LedgerDAO.create("家庭账本")
    scoped = BudgetDAO.upsert(USER_A, "2026-01", "餐饮", 300.0, other["id"])
    assert scoped["ledger_id"] == other["id"]

    # 同一账本内重复键仍被唯一约束拦截
    with pytest.raises(IntegrityError):
        with Session(v7_engine) as session:
            session.add(
                Budget(
                    user_id=USER_A,
                    ledger_id=other["id"],
                    month="2026-01",
                    category="餐饮",
                    amount=1.0,
                )
            )
            session.commit()


def test_budget_unique_upgrade_sql_per_dialect():
    """MySQL / PostgreSQL 的唯一键升级 SQL（无真实实例，按生成文本做语义断言）"""
    mysql = _budget_unique_upgrade_sql("mysql")
    assert mysql[0] == "ALTER TABLE budgets DROP INDEX uq_budget_scope"
    assert mysql[1] == (
        "ALTER TABLE budgets ADD UNIQUE KEY uq_budget_scope "
        "(user_id, ledger_id, month, category)"
    )

    postgres = _budget_unique_upgrade_sql("postgresql")
    assert postgres[0] == (
        "ALTER TABLE budgets DROP CONSTRAINT IF EXISTS uq_budget_scope"
    )
    assert postgres[1] == (
        "ALTER TABLE budgets ADD CONSTRAINT uq_budget_scope "
        "UNIQUE (user_id, ledger_id, month, category)"
    )

    # SQLite 不支持改约束，走重建表分支（不返回 ALTER 语句）
    assert _budget_unique_upgrade_sql("sqlite") == []


def test_budget_unique_check_sql_covers_all_dialects():
    """三方言的「唯一键是否已含 ledger_id」判定语句都指向同一语义"""
    for dialect in ("sqlite", "mysql", "postgresql"):
        sql = _budget_unique_ledger_check_sql(dialect)
        assert "ledger_id" in sql
        assert "budgets" in sql
