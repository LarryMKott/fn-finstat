"""Schema 版本管理与升级迁移

- _MIGRATIONS 登记从 vN → vN+1 的迁移函数（签名 fn(session: Session)），
  启动时按序应用；每个迁移独立事务、迁移完成后立即写版本戳，
  中断重启自动从断点继续
- 注意：MySQL 的 DDL 会隐式提交无法回滚，迁移函数应写成幂等（可重复执行）
"""

from typing import Callable, Optional

from sqlalchemy import delete, inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from app.db.ledgers import ensure_default_ledger
from app.db.models import AppMeta, Budget

BASELINE_SCHEMA_VERSION = (
    1  # 0.2.x 建表即该版本（bills + categories），无版本记录的老库按此补记
)
LATEST_SCHEMA_VERSION = 13
SCHEMA_VERSION_KEY = "schema_version"

# 账本维度（v8）涉及的表与索引名（索引名与模型的 index=True 生成规则一致：ix_<表>_<列>）
_LEDGER_TABLES = (
    ("bills", "ix_bills_ledger_id"),
    ("budgets", "ix_budgets_ledger_id"),
    ("asset_snapshots", "ix_asset_snapshots_ledger_id"),
)

# budgets 唯一键名（升级前后同名，便于按名字判定是否已含 ledger_id）
_BUDGET_UNIQUE = "uq_budget_scope"


def _v2_add_user_id(session: Session) -> None:
    """v1 → v2：账单按飞牛账号区分，bills 增加 user_id 列

    历史数据（升级前已存在）归入空串默认账号；网关用户可在设置页一键认领。
    """
    dialect = session.bind.dialect.name
    col_type = "TEXT" if dialect == "sqlite" else "VARCHAR(32)"
    session.execute(
        text(f"ALTER TABLE bills ADD COLUMN user_id {col_type} NOT NULL DEFAULT ''")
    )
    if dialect == "mysql":
        # MySQL 无 CREATE INDEX IF NOT EXISTS 且 DDL 隐式提交，查 statistics 表保证幂等
        exists = session.execute(
            text(
                "SELECT 1 FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = 'bills' "
                "AND index_name = 'idx_bills_user_id'"
            )
        ).first()
        if not exists:
            session.execute(text("CREATE INDEX idx_bills_user_id ON bills(user_id)"))
    else:
        session.execute(
            text("CREATE INDEX IF NOT EXISTS idx_bills_user_id ON bills(user_id)")
        )


def _mysql_column_exists(session: Session, table: str, column: str) -> bool:
    """MySQL 无 ALTER ADD COLUMN IF NOT EXISTS，用 information_schema 保证幂等"""
    return (
        session.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t "
                "AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).first()
        is not None
    )


def _sqlite_column_exists(session: Session, table: str, column: str) -> bool:
    return (
        session.execute(
            text("SELECT 1 FROM pragma_table_info(:t) WHERE name = :c"),
            {"t": table, "c": column},
        ).first()
        is not None
    )


def _add_column_if_missing(session: Session, table: str, column: str, ddl: str) -> None:
    """跨方言幂等加列：PG 用 ADD COLUMN IF NOT EXISTS，MySQL/SQLite 先查列存在再执行

    ddl 为不含 IF NOT EXISTS 的完整 ALTER 语句；PG 方言自动在 ADD COLUMN 之后
    插入 IF NOT EXISTS（PG 语法要求其位于列名之前，不能追加在语句末尾）。
    """
    dialect = session.bind.dialect.name
    if dialect == "postgresql":
        session.execute(text(ddl.replace("ADD COLUMN", "ADD COLUMN IF NOT EXISTS", 1)))
    elif dialect == "mysql":
        if not _mysql_column_exists(session, table, column):
            session.execute(text(ddl))
    else:
        if not _sqlite_column_exists(session, table, column):
            session.execute(text(ddl))


def _v3_add_tags_budget_assets(session: Session) -> None:
    """v2 → v3：bills 增加标签/报销/回收站列；budgets、asset_snapshots 新表

    新表由 init_db 的 Base.metadata.create_all 幂等创建，本迁移只负责给既有 bills 补列。
    """
    dialect = session.bind.dialect.name
    bool_default = "FALSE" if dialect == "postgresql" else "0"
    _add_column_if_missing(
        session,
        "bills",
        "tags",
        "ALTER TABLE bills ADD COLUMN tags "
        + ("TEXT" if dialect == "sqlite" else "VARCHAR(255)")
        + " NOT NULL DEFAULT ''",
    )
    _add_column_if_missing(
        session,
        "bills",
        "reimbursed",
        f"ALTER TABLE bills ADD COLUMN reimbursed BOOLEAN NOT NULL DEFAULT {bool_default}",
    )
    _add_column_if_missing(
        session,
        "bills",
        "deleted",
        f"ALTER TABLE bills ADD COLUMN deleted BOOLEAN NOT NULL DEFAULT {bool_default}",
    )


def _v4_add_automation_tables(session: Session) -> None:
    """v3 → v4：自动化底座新表（scheduled_tasks / task_runs / imported_files）

    全部为新增表，由 init_db 的 Base.metadata.create_all 按方言幂等创建，
    本迁移只负责推进版本戳（与 v3 的 budgets/asset_snapshots 同策略）。
    """
    _ = session  # 新表建表由 create_all 完成，无列级变更


def _v5_add_ai_reports_table(session: Session) -> None:
    """v4 → v5：AI 报告归档新表 ai_reports

    新表由 init_db 的 Base.metadata.create_all 按方言幂等创建（含
    uq_ai_report_scope 唯一约束），本迁移只负责推进版本戳，沿用 v4 策略。
    """
    _ = session  # 新表建表由 create_all 完成，无列级变更


def _v6_add_notification_tables(session: Session) -> None:
    """v5 → v6：通知中心新表（notifications / notification_reads）

    全部为新增表，由 init_db 的 Base.metadata.create_all 按方言幂等创建
    （notifications.event_key 唯一约束做事件去重），本迁移只负责推进版本戳，
    沿用 v4/v5 策略。
    """
    _ = session  # 新表建表由 create_all 完成，无列级变更


def _v7_add_learned_rules_table(session: Session) -> None:
    """v6 → v7：分类自学习新表 learned_rules（T-6.3）

    新表由 init_db 的 Base.metadata.create_all 按方言幂等创建（含
    uq_learned_rule_pattern_category 唯一约束做 (pattern, category) 去重），
    本迁移只负责推进版本戳，沿用 v4/v5/v6 策略。
    """
    _ = session  # 新表建表由 create_all 完成，无列级变更


def _create_index_if_missing(
    session: Session, table: str, index: str, columns: str
) -> None:
    """跨方言幂等建索引：已存在同名索引则跳过

    MySQL 没有 CREATE INDEX IF NOT EXISTS 且 DDL 隐式提交，因此统一走
    「先查已存在索引名、再决定是否执行」，不用 IF NOT EXISTS 语法。
    """
    try:
        existing = {i["name"] for i in inspect(session.bind).get_indexes(table)}
    except Exception:  # pragma: no cover - 反射失败时按已存在处理，避免重复建索引报错
        return
    if index in existing:
        return
    session.execute(text(f"CREATE INDEX {index} ON {table}({columns})"))


def _budget_unique_ledger_check_sql(dialect: str) -> str:
    """判定 budgets 唯一键是否已含 ledger_id 的方言化查询（有结果即已含）

    独立成「返回 SQL 文本」的纯函数，便于 MySQL / PostgreSQL 在无真实实例时
    直接对生成的 SQL 做断言测试（三方言语义对齐的验证策略见开发计划 4.1）。
    """
    if dialect == "sqlite":
        # SQLite 把 UNIQUE 约束实现为自动索引（名字恒为 sqlite_autoindex_budgets_N，
        # 不保留约束名），因此按「唯一索引覆盖的列」判定而非按约束名判定
        return (
            "SELECT 1 FROM pragma_index_list('budgets') AS il "
            "JOIN pragma_index_info(il.name) AS ii "
            "WHERE il.\"unique\" = 1 AND ii.name = 'ledger_id'"
        )
    if dialect == "mysql":
        return (
            "SELECT 1 FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = 'budgets' "
            f"AND index_name = '{_BUDGET_UNIQUE}' AND column_name = 'ledger_id'"
        )
    return (
        "SELECT 1 FROM information_schema.constraint_column_usage "
        f"WHERE table_name = 'budgets' AND constraint_name = '{_BUDGET_UNIQUE}' "
        "AND column_name = 'ledger_id'"
    )


def _budget_unique_upgrade_sql(dialect: str) -> list[str]:
    """把 budgets 唯一键升级为含 ledger_id 的 DDL（SQLite 走重建表，返回空列表）

    - MySQL：唯一键即唯一索引，先 DROP INDEX 再 ADD UNIQUE KEY（DDL 隐式提交，
      故必须靠上一步的存在性检查保证幂等）
    - PostgreSQL：DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT，语句自带幂等
    """
    columns = "user_id, ledger_id, month, category"
    if dialect == "sqlite":
        return []  # SQLite 无法改约束，走 _rebuild_budgets_sqlite
    if dialect == "mysql":
        return [
            f"ALTER TABLE budgets DROP INDEX {_BUDGET_UNIQUE}",
            f"ALTER TABLE budgets ADD UNIQUE KEY {_BUDGET_UNIQUE} ({columns})",
        ]
    return [
        f"ALTER TABLE budgets DROP CONSTRAINT IF EXISTS {_BUDGET_UNIQUE}",
        f"ALTER TABLE budgets ADD CONSTRAINT {_BUDGET_UNIQUE} UNIQUE ({columns})",
    ]


def _rebuild_budgets_sqlite(session: Session) -> None:
    """SQLite 重建 budgets 表以变更唯一约束（唯一键含 ledger_id）

    SQLite 既不支持 ALTER CONSTRAINT，也不支持 DROP CONSTRAINT，只能
    「建新表 → 搬数据 → 删旧表 → 改名」。新表 DDL 由 ORM 模型按方言编译生成
    （含主键、唯一约束、列类型），避免手写 DDL 与模型漂移；索引在改名后按
    模型的 Index 对象补建（旧表删除时其索引一并消失）。
    id 原样搬迁，保证预算行的外部引用（前端列表项 id）不变。
    """
    create_sql = str(
        CreateTable(Budget.__table__).compile(dialect=session.bind.dialect)
    )
    create_sql = create_sql.replace(
        "CREATE TABLE budgets", "CREATE TABLE budgets_new", 1
    )
    session.execute(text(create_sql))
    session.execute(
        text(
            "INSERT INTO budgets_new (id, user_id, ledger_id, month, category, amount) "
            "SELECT id, user_id, ledger_id, month, category, amount FROM budgets"
        )
    )
    session.execute(text("DROP TABLE budgets"))
    session.execute(text("ALTER TABLE budgets_new RENAME TO budgets"))
    # 索引必须复用会话自身的连接：另开连接会在会话写事务未提交时撞 SQLite 库锁
    connection = session.connection()
    for idx in Budget.__table__.indexes:
        idx.create(bind=connection, checkfirst=True)


def _v8_add_ledger_dimension(session: Session) -> None:
    """v7 → v8：账本维度（T-7.1）—— ledgers 表 + 三张业务表的 ledger_id

    升级等价性（v0.7 出口标准的底线）：迁移中先建默认账本，再用它的 id 作为
    ledger_id 列的 DDL 默认值与回充值，历史流水 / 预算 / 资产快照全部挂到默认
    账本上。因此升级后「不传 ledger_id 的旧调用」与升级前行为完全一致。

    三方言差异集中在 budgets 唯一键：
    - SQLite：不支持改约束 → 重建表 + 数据搬迁（_rebuild_budgets_sqlite）
    - MySQL：DROP INDEX + ADD UNIQUE KEY（DDL 隐式提交，靠存在性检查保证幂等）
    - PostgreSQL：DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT（语句自带幂等）
    """
    default_id = ensure_default_ledger(session)
    for table, index in _LEDGER_TABLES:
        _add_column_if_missing(
            session,
            table,
            "ledger_id",
            f"ALTER TABLE {table} ADD COLUMN ledger_id INTEGER NOT NULL DEFAULT {default_id}",
        )
        # 显式回填兜底：部分方言（MySQL < 8.0.12 / PG < 11）加列时对既有行的
        # 默认值填充行为不一致，这里按默认账本 id 统一补一遍
        session.execute(
            text(
                f"UPDATE {table} SET ledger_id = :lid WHERE ledger_id IS NULL OR ledger_id = 0"
            ),
            {"lid": default_id},
        )
        _create_index_if_missing(session, table, index, "ledger_id")

    dialect = session.bind.dialect.name
    if session.execute(text(_budget_unique_ledger_check_sql(dialect))).first() is None:
        statements = _budget_unique_upgrade_sql(dialect)
        if not statements:  # SQLite：走重建表分支
            _rebuild_budgets_sqlite(session)
        else:
            for statement in statements:
                session.execute(text(statement))


def _v9_add_family_tables(session: Session) -> None:
    """v8 → v9：家庭空间（T-7.2）—— families / family_members 新表

    新表建表由 init_db 的 Base.metadata.create_all 按方言幂等创建
    （family_members.user_id 全局唯一保证一个账号至多加入一个家庭），
    本迁移只推进版本戳，与 v4/v5/v6/v7 的纯新表模式一致。
    """
    _ = session


def _v10_add_family_budget(session: Session) -> None:
    """v9 → v10：家庭预算（T-7.3）—— budgets 加 family_id 可空列

    家庭预算行的 user_id 用合成属主（constants.family_scope_user），
    复用既有唯一键做家庭内去重，因此本迁移只需加列 + 索引；个人预算行
    family_id 恒为 NULL，升级零回填。
    """
    _add_column_if_missing(
        session,
        "budgets",
        "family_id",
        "ALTER TABLE budgets ADD COLUMN family_id INTEGER",
    )
    _create_index_if_missing(session, "budgets", "ix_budgets_family_id", "family_id")


def _v11_add_reimbursements(session: Session) -> None:
    """v10 → v11：报销 / 垫付工作流（T-7.4）

    reimbursements 新表由 init_db 的 create_all 幂等创建；本迁移给 bills 加
    reimb_id 关联列，并把存量 reimbursed=1 的流水按账号归入「历史报销」
    （状态已结清），报销标记保留——既有筛选与导出口径零破坏。
    """
    _add_column_if_missing(
        session,
        "bills",
        "reimb_id",
        "ALTER TABLE bills ADD COLUMN reimb_id INTEGER",
    )
    _create_index_if_missing(session, "bills", "ix_bills_reimb_id", "reimb_id")

    # 存量兼容迁移：每个有已报销流水的账号建一张「历史报销」（已结清）
    legacy_users = session.execute(
        text(
            "SELECT user_id, COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total "
            "FROM bills WHERE reimbursed = 1 AND deleted = 0 AND reimb_id IS NULL "
            "GROUP BY user_id"
        )
    ).all()
    if not legacy_users:
        return
    import time as _time

    for user_id, _cnt, total in legacy_users:
        session.execute(
            text(
                "INSERT INTO reimbursements "
                "(user_id, title, status, note, received_amount, received_date, created_at) "
                "VALUES (:uid, :title, 'settled', :note, :total, NULL, :ts)"
            ),
            {
                "uid": user_id,
                "title": "历史报销",
                "note": "由「报销标记」自动迁移",
                "total": float(total),
                "ts": _time.time(),
            },
        )
        claim_id = session.execute(
            text(
                "SELECT id FROM reimbursements "
                "WHERE user_id = :uid AND title = '历史报销'"
            ),
            {"uid": user_id},
        ).scalar_one()
        session.execute(
            text(
                "UPDATE bills SET reimb_id = :cid "
                "WHERE user_id = :uid AND reimbursed = 1 AND deleted = 0 "
                "AND reimb_id IS NULL"
            ),
            {"cid": claim_id, "uid": user_id},
        )


def _v12_add_loan_tables(session: Session) -> None:
    """v11 → v12：借贷台账（T-7.5）—— loans / loan_payments 新表

    纯新表模式：由 init_db 的 create_all 幂等创建，本迁移只推进版本戳
    （与 v4/v5/v6/v7/v9 一致）。
    """
    _ = session


def _v13_add_audit_logs(session: Session) -> None:
    """v12 → v13：操作审计（T-7.6）—— audit_logs 新表

    纯新表模式：由 init_db 的 create_all 幂等创建，本迁移只推进版本戳。
    审计表不参与备份导出，replace 恢复也不清空本表（审计链完整）。
    """
    _ = session


_MIGRATIONS: dict[int, Callable[[Session], None]] = {
    1: _v2_add_user_id,
    2: _v3_add_tags_budget_assets,
    3: _v4_add_automation_tables,
    4: _v5_add_ai_reports_table,
    5: _v6_add_notification_tables,
    6: _v7_add_learned_rules_table,
    7: _v8_add_ledger_dimension,
    8: _v9_add_family_tables,
    9: _v10_add_family_budget,
    10: _v11_add_reimbursements,
    11: _v12_add_loan_tables,
    12: _v13_add_audit_logs,
}


def read_schema_version(session: Session) -> Optional[int]:
    """读取 app_meta 中记录的 schema 版本；无记录返回 None"""
    value = session.scalar(
        select(AppMeta.meta_value).where(AppMeta.meta_key == SCHEMA_VERSION_KEY)
    )
    return int(value) if value is not None else None


def set_schema_version(session: Session, version: int) -> None:
    """覆盖写入 schema 版本戳（flush 立即生效，随调用方事务提交）"""
    session.execute(delete(AppMeta).where(AppMeta.meta_key == SCHEMA_VERSION_KEY))
    session.add(AppMeta(meta_key=SCHEMA_VERSION_KEY, meta_value=str(version)))
    session.flush()
