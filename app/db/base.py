"""数据库引擎管理、schema 版本迁移与初始化（SQLAlchemy ORM）

三方言（SQLite / MySQL / PostgreSQL）共用 app/db/models.py 中的一套 ORM 模型，
方言差异（去重插入、序列同步、驱动懒加载）封装在本模块与 app/db/dialects.py。

运行期数据库切换（详见 init_db 与设置服务）：
- 引擎在 init_db 时按生效配置创建；设置页「迁移并切换」成功后可运行期整体切换
- app_meta 表记录 schema_version；0.2.x 老库无此表，按基线版本补记后逐版本迁移
- 每个迁移独立事务、迁移后立即写版本戳，中断重启可从断点继续
- SQLite 在应用迁移前自动备份 bill.db；数据库类型变更时旧 SQLite 数据自动搬移
"""
import importlib
import json
import logging
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

from sqlalchemy import create_engine, delete, event, func, inspect, select, text
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import (
    DATA_DIR, DB_PATH, DEFAULT_CATEGORIES, DBSettings, effective_db_settings,
)
from app.db.models import AppMeta, Base, Bill, Category

logger = logging.getLogger(__name__)

BASELINE_SCHEMA_VERSION = 1  # 0.2.x 建表即该版本（bills + categories），无版本记录的老库按此补记
LATEST_SCHEMA_VERSION = 2
_SCHEMA_VERSION_KEY = "schema_version"

# 驱动缺失时的用户指引（与 cmd/config_callback 安装的包保持一致）
_DRIVER_HINTS = {
    "mysql": ("pymysql", "pip install PyMySQL cryptography"),
    "postgresql": ("psycopg2", "pip install psycopg2-binary"),
}


class UniqueViolationError(Exception):
    """唯一约束冲突（各驱动统一转换为本异常，服务层转 400）"""


def as_unique_violation(exc: IntegrityError) -> UniqueViolationError:
    return UniqueViolationError(str(exc.orig or exc))


def _require_driver(db_type: str) -> None:
    """驱动懒加载检查：缺失时给出明确安装指引（设置页迁移会先尝试自动安装）"""
    if db_type not in _DRIVER_HINTS:
        return
    module = _DRIVER_HINTS[db_type][0]
    try:
        importlib.import_module(module)
    except ImportError as exc:
        raise RuntimeError(
            f"未安装 {db_type} 驱动：请在设置页重新发起迁移（会自动安装驱动），"
            f"或手动执行 {_DRIVER_HINTS[db_type][1]}"
        ) from exc


def engine_url(settings: DBSettings) -> URL:
    """按方言构造连接 URL（密码等特殊字符由 URL.create 转义）"""
    if settings.db_type == "mysql":
        return URL.create(
            "mysql+pymysql", username=settings.user, password=settings.password,
            host=settings.host, port=settings.port, database=settings.name,
            query={"charset": "utf8mb4"},
        )
    if settings.db_type == "postgresql":
        return URL.create(
            "postgresql+psycopg2", username=settings.user, password=settings.password,
            host=settings.host, port=settings.port, database=settings.name,
        )
    return URL.create("sqlite", database=DB_PATH.as_posix())


def build_engine(settings: DBSettings) -> Engine:
    """按连接配置创建引擎；外部数据库驱动缺失时抛出带指引的 RuntimeError"""
    _require_driver(settings.db_type)
    if settings.db_type == "sqlite":
        engine = create_engine(
            engine_url(settings),
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )

        @event.listens_for(engine, "connect")
        def _sqlite_pragma(dbapi_conn, _):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        return engine
    # 外部数据库：连接池复用 + 心跳检活 + 回收长连接（防 MySQL wait_timeout 断连）
    return create_engine(
        engine_url(settings),
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"connect_timeout": 10},
    )


class _EngineState:
    """当前生效的引擎与会话工厂；运行期切换时整体替换（在途会话继续用旧引擎直至关闭）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._settings: Optional[DBSettings] = None
        self._engine: Optional[Engine] = None
        self._factory: Optional[sessionmaker] = None

    def settings(self) -> Optional[DBSettings]:
        with self._lock:
            return self._settings

    def engine(self) -> Optional[Engine]:
        with self._lock:
            return self._engine

    def activate(self, settings: DBSettings, engine: Engine) -> Optional[Engine]:
        """启用新引擎，返回被替换的旧引擎（调用方负责 dispose）"""
        with self._lock:
            old = self._engine
            self._settings = settings
            self._engine = engine
            self._factory = sessionmaker(bind=engine, expire_on_commit=False)
            return old

    def new_session(self) -> Session:
        with self._lock:
            factory = self._factory
        if factory is None:
            raise RuntimeError("数据库尚未初始化：init_db 未完成")
        return factory()


_STATE = _EngineState()


def current_settings() -> DBSettings:
    settings = _STATE.settings()
    if settings is None:
        return effective_db_settings()
    return settings


def current_engine() -> Engine:
    engine = _STATE.engine()
    if engine is None:
        raise RuntimeError("数据库尚未初始化：init_db 未完成")
    return engine


def activate_engine(settings: DBSettings, engine: Engine) -> None:
    """运行期切换到新引擎（迁移成功后调用），旧引擎关闭空闲连接"""
    old = _STATE.activate(settings, engine)
    if old is not None:
        old.dispose()
    logger.info("数据库引擎已切换为 %s", settings.db_type)


@contextmanager
def get_db() -> Iterator[Session]:
    """事务化 ORM 会话：正常结束提交，异常回滚，连接由引擎连接池管理"""
    session = _STATE.new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def insert_ignore_rows(conn, table, rows: list[dict]) -> int:
    """批量插入并在唯一约束冲突时跳过冲突行；返回驱动报告的影响行数（不可靠时为 -1）

    三方言等价实现：MySQL INSERT IGNORE / PG ON CONFLICT DO NOTHING / SQLite OR IGNORE。
    """
    if not rows:
        return 0
    dialect = conn.dialect.name
    if dialect == "mysql":
        from sqlalchemy.dialects.mysql import insert
        stmt = insert(table).prefix_with("IGNORE")
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
        stmt = insert(table).on_conflict_do_nothing()
    else:
        from sqlalchemy.dialects.sqlite import insert
        stmt = insert(table).prefix_with("OR IGNORE")
    return conn.execute(stmt, rows).rowcount


# ---- Schema 版本与升级迁移 ----
# - _MIGRATIONS 登记从 vN → vN+1 的迁移函数（签名 fn(session: Session)），启动时按序应用
# - 每个迁移独立事务、迁移完成后立即写版本戳：中断重启自动从断点继续
# - 注意：MySQL 的 DDL 会隐式提交无法回滚，迁移函数应写成幂等（可重复执行）
def _v2_add_user_id(session: Session) -> None:
    """v1 → v2：账单按飞牛账号区分，bills 增加 user_id 列

    历史数据（升级前已存在）归入空串默认账号；网关用户可在设置页一键认领。
    """
    dialect = session.bind.dialect.name
    col_type = "TEXT" if dialect == "sqlite" else "VARCHAR(32)"
    session.execute(text(f"ALTER TABLE bills ADD COLUMN user_id {col_type} NOT NULL DEFAULT ''"))
    if dialect == "mysql":
        # MySQL 无 CREATE INDEX IF NOT EXISTS 且 DDL 隐式提交，查 statistics 表保证幂等
        exists = session.execute(text(
            "SELECT 1 FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = 'bills' "
            "AND index_name = 'idx_bills_user_id'"
        )).first()
        if not exists:
            session.execute(text("CREATE INDEX idx_bills_user_id ON bills(user_id)"))
    else:
        session.execute(text("CREATE INDEX IF NOT EXISTS idx_bills_user_id ON bills(user_id)"))


_MIGRATIONS: dict[int, Callable[[Session], None]] = {1: _v2_add_user_id}


def _get_schema_version(session: Session) -> Optional[int]:
    value = session.scalar(select(AppMeta.meta_value).where(AppMeta.meta_key == _SCHEMA_VERSION_KEY))
    return int(value) if value is not None else None


def set_schema_version(session: Session, version: int) -> None:
    session.execute(delete(AppMeta).where(AppMeta.meta_key == _SCHEMA_VERSION_KEY))
    session.add(AppMeta(meta_key=_SCHEMA_VERSION_KEY, meta_value=str(version)))
    session.flush()


# ---- 数据库类型标记（与数据库本身解耦，切换后旧库可能无法再连接）----
_DB_TYPE_MARKER = DATA_DIR / "db_meta.json"


def read_db_type_marker() -> Optional[str]:
    try:
        return json.loads(_DB_TYPE_MARKER.read_text(encoding="utf-8")).get("db_type")
    except FileNotFoundError:
        return None
    except Exception:
        logger.warning("数据库类型标记文件损坏，按首次安装处理：%s", _DB_TYPE_MARKER)
        return None


def write_db_type_marker(settings: Optional[DBSettings] = None) -> None:
    settings = settings or current_settings()
    try:
        _DB_TYPE_MARKER.write_text(json.dumps({"db_type": settings.db_type}), encoding="utf-8")
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
            "SELECT meta_value FROM app_meta WHERE meta_key = ?", (_SCHEMA_VERSION_KEY,)
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
            "数据仍保留在原数据库中，如需找回请恢复原向导配置或使用数据库工具手动导出。", previous,
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
        stats["copied_bills"], stats["copied_categories"], DB_PATH,
    )


def init_db() -> None:
    """建表、应用 schema 迁移并预置默认分类，幂等可重复执行

    版本判定：
        app_meta 有记录  → 以记录为准，逐版本应用 _MIGRATIONS 至最新
        无记录但有 bills 表（0.2.x 老库升级）→ 按基线版本补记后照常迁移
        无记录且无表（首次安装）→ 建表后直接记为最新版本
    默认分类仅在分类表为空时预置，尊重用户对默认分类的删除/改名。
    """
    settings = effective_db_settings()
    _sqlite_backup_if_pending(settings)
    engine = build_engine(settings)

    had_bills = inspect(engine).has_table("bills")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        current = _get_schema_version(session)
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
            raise RuntimeError(f"数据库升级缺少 v{current} → v{current + 1} 的迁移实现，已停止启动以保护数据")
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
                session.connection(), Category.__table__, [{"name": name} for name in DEFAULT_CATEGORIES]
            )
    write_db_type_marker(settings)
    logger.info("数据库就绪（%s，schema v%d）", settings.db_type, current)
