"""Schema 版本管理与升级迁移

- _MIGRATIONS 登记从 vN → vN+1 的迁移函数（签名 fn(session: Session)），
  启动时按序应用；每个迁移独立事务、迁移完成后立即写版本戳，
  中断重启自动从断点继续
- 注意：MySQL 的 DDL 会隐式提交无法回滚，迁移函数应写成幂等（可重复执行）
"""

from typing import Callable, Optional

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.db.models import AppMeta

BASELINE_SCHEMA_VERSION = (
    1  # 0.2.x 建表即该版本（bills + categories），无版本记录的老库按此补记
)
LATEST_SCHEMA_VERSION = 6
SCHEMA_VERSION_KEY = "schema_version"


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


_MIGRATIONS: dict[int, Callable[[Session], None]] = {
    1: _v2_add_user_id,
    2: _v3_add_tags_budget_assets,
    3: _v4_add_automation_tables,
    4: _v5_add_ai_reports_table,
    5: _v6_add_notification_tables,
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
