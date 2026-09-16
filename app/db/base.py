"""数据库层入口：建库初始化、数据库类型标记，并作为引擎/迁移模块的统一门面

模块拆分（本文件保留门面再导出，调用方 `from app.db.base import X` 不受影响）：
- app/db/engine.py     引擎构建、运行期切换、会话管理、去重插入
- app/db/migrations.py schema 版本迁移
- app/db/models.py     ORM 模型（三方言共用一套）

运行期数据库切换（详见 init_db 与设置服务）：
- 引擎在 init_db 时按生效配置创建；设置页「迁移并切换」成功后可运行期整体切换
- app_meta 表记录 schema_version；0.2.x 老库无此表，按基线版本补记后逐版本迁移
- 每个迁移独立事务、迁移后立即写版本戳，中断重启可从断点继续
- SQLite 在应用迁移前自动备份 bill.db；数据库类型变更时旧 SQLite 数据自动搬移
"""

import json
import logging
import shutil
import sqlite3
from typing import Optional

from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from app.config import (
    DATA_DIR,
    DB_PATH,
    DEFAULT_CATEGORIES,
    DBSettings,
    effective_db_settings,
)
from app.db.engine import (
    activate_engine,
    build_engine,
    current_engine,
    current_settings,
    get_db,
    in_chunks,
    insert_ignore_rows,
    is_unique_violation,
    new_session,
    translate_unique_violation,
)
from app.db.models import (
    Base,
    Category,
)
from app.db.migrations import (
    BASELINE_SCHEMA_VERSION,
    LATEST_SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
    read_schema_version,
    set_schema_version,
)

logger = logging.getLogger(__name__)

# ---- 门面再导出：既有调用方统一从 app.db.base 导入 ----
# 注意：不导出 engine._STATE（私有内部状态）；测试需要时从 app.db.engine 导入
__all__ = [
    "BASELINE_SCHEMA_VERSION",
    "LATEST_SCHEMA_VERSION",
    "activate_engine",
    "build_engine",
    "current_engine",
    "current_settings",
    "get_db",
    "in_chunks",
    "init_db",
    "insert_ignore_rows",
    "is_unique_violation",
    "new_session",
    "read_schema_version",
    "set_schema_version",
    "translate_unique_violation",
    "write_db_type_marker",
    "read_db_type_marker",
]


# ---- 数据库类型标记（与数据库本身解耦，切换后旧库可能无法再连接）----
_DB_TYPE_MARKER = DATA_DIR / "db_meta.json"


def read_db_type_marker() -> Optional[str]:
    """读取上次记录的数据库类型；无记录或文件损坏返回 None（按首次安装处理）"""
    try:
        return json.loads(_DB_TYPE_MARKER.read_text(encoding="utf-8")).get("db_type")
    except FileNotFoundError:
        return None
    except Exception:
        logger.warning("数据库类型标记文件损坏，按首次安装处理：%s", _DB_TYPE_MARKER)
        return None


def write_db_type_marker(settings: Optional[DBSettings] = None) -> None:
    """记录当前数据库类型；写入失败仅告警不阻断（标记仅用于类型变更检测）"""
    settings = settings or current_settings()
    try:
        _DB_TYPE_MARKER.write_text(
            json.dumps({"db_type": settings.db_type}), encoding="utf-8"
        )
    except Exception:
        logger.warning("写入数据库类型标记失败（不影响运行）：%s", _DB_TYPE_MARKER)


def _sqlite_backup_if_pending(settings: DBSettings) -> None:
    """SQLite 专有：存在待应用迁移时，先 checkpoint 并备份 bill.db（迁移前的安全快照）"""
    if settings.db_type != "sqlite" or not DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS app_meta (meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL)"
        )
        row = conn.execute(
            "SELECT meta_value FROM app_meta WHERE meta_key = ?",
            (SCHEMA_VERSION_KEY,),
        ).fetchone()
        if row is None:
            has_bills = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'bills'"
            ).fetchone()
            if not has_bills:
                return  # 全新库：无数据可迁
            current = BASELINE_SCHEMA_VERSION
        else:
            current = int(row[0])
        if current >= LATEST_SCHEMA_VERSION:
            return
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    backup = DB_PATH.with_name(f"bill.db.bak-v{current}")
    shutil.copy2(DB_PATH, backup)
    logger.info("检测到待应用迁移，已备份数据库：%s", backup)


def _handle_db_type_switch(previous: str, settings: DBSettings) -> None:
    """数据库类型变更时的数据迁移（向导改配置重启后触发）：旧库为 SQLite 则自动搬移

    - 仅当新库为空时搬移（避免两库数据混杂）；旧 bill.db 文件保留不动，可随时回退
    - 旧库为外部数据库时连接参数已随向导失效，无法自动迁移，提示用户处理
    """
    if previous == settings.db_type:
        return
    logger.warning("检测到数据库类型变更：%s -> %s", previous, settings.db_type)
    if previous != "sqlite":
        logger.warning(
            "旧 %s 库中的数据无法自动迁移（历史连接参数已失效）。"
            "数据仍保留在原数据库中，如需找回请恢复原向导配置或使用数据库工具手动导出。",
            previous,
        )
        return
    if not DB_PATH.exists():
        return
    from app.db.copy import copy_database  # 局部导入避免循环依赖

    source = create_engine(f"sqlite:///{DB_PATH.as_posix()}")
    try:
        stats = copy_database(source, current_engine(), LATEST_SCHEMA_VERSION)
    finally:
        source.dispose()
    if stats["target_had_data"]:
        logger.warning("新数据库非空，已按去重合并搬移；旧数据仍保留在 %s", DB_PATH)
    logger.info(
        "已从旧 SQLite 库搬移 %s 条流水、%s 个分类（源文件保留于 %s）",
        stats["copied_bills"],
        stats["copied_categories"],
        DB_PATH,
    )


def init_db() -> None:
    """建表、应用 schema 迁移并预置默认分类，幂等可重复执行

    版本判定：
        app_meta 有记录  → 以记录为准，逐版本应用 _MIGRATIONS 至最新
        无记录但有 bills 表（0.2.x 老库升级）→ 按基线版本补记后照常迁移
        无记录且无表（首次安装）→ 建表后直接记为最新版本
    默认分类仅在分类表为空时预置，尊重用户对默认分类的删除/改名。
    """
    from app.db.migrations import _MIGRATIONS

    settings = effective_db_settings()
    _sqlite_backup_if_pending(settings)
    engine = build_engine(settings)

    had_bills = inspect(engine).has_table("bills")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        current = read_schema_version(session)
        if current is None:
            current = BASELINE_SCHEMA_VERSION if had_bills else LATEST_SCHEMA_VERSION
            set_schema_version(session, current)
            session.commit()
            if had_bills:
                logger.info("检测到 v0.2.x 老库，schema 版本补记为 v%d", current)

    # 逐版本应用迁移（每个迁移独立事务，失败时已完成的版本保留，重启续迁）
    while current < LATEST_SCHEMA_VERSION:
        migrate = _MIGRATIONS.get(current)
        if migrate is None:
            raise RuntimeError(
                f"数据库升级缺少 v{current} → v{current + 1} 的迁移实现，已停止启动以保护数据"
            )
        with Session(engine) as session:
            migrate(session)
            set_schema_version(session, current + 1)
            session.commit()
        logger.info("数据库 schema 已从 v%d 迁移到 v%d", current, current + 1)
        current += 1

    activate_engine(settings, engine)

    previous_type = read_db_type_marker()
    if previous_type is not None:
        _handle_db_type_switch(previous_type, settings)

    with get_db() as session:
        if session.scalar(select(func.count()).select_from(Category)) == 0:
            insert_ignore_rows(
                session.connection(),
                Category.__table__,
                [{"name": name} for name in DEFAULT_CATEGORIES],
            )
    write_db_type_marker(settings)
    logger.info("数据库就绪（%s，schema v%d）", settings.db_type, current)
