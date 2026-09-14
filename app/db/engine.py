"""数据库引擎管理：连接 URL 构建、驱动懒加载、运行期引擎切换与会话管理

三方言（SQLite / MySQL / PostgreSQL）共用 app/db/models.py 中的一套 ORM 模型，
方言差异（去重插入、序列同步、驱动懒加载）封装在本模块。
"""

import importlib
import logging
import threading
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import DB_PATH, DBSettings, effective_db_settings
from app.core.errors import ConflictError, ErrorCode

logger = logging.getLogger(__name__)

# 驱动缺失时的用户指引（与 cmd/config_callback 安装的包保持一致）
_DRIVER_HINTS = {
    "mysql": ("pymysql", "pip install PyMySQL cryptography"),
    "postgresql": ("psycopg2", "pip install psycopg2-binary"),
}

# 各驱动唯一约束冲突的报错文案（is_unique_violation 判定依据）
_UNIQUE_MARKERS = (
    "UNIQUE constraint failed",  # SQLite
    "Duplicate entry",  # MySQL
    "duplicate key value violates unique constraint",  # PostgreSQL
)


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
            "mysql+pymysql",
            username=settings.user,
            password=settings.password,
            host=settings.host,
            port=settings.port,
            database=settings.name,
            query={"charset": "utf8mb4"},
        )
    if settings.db_type == "postgresql":
        return URL.create(
            "postgresql+psycopg2",
            username=settings.user,
            password=settings.password,
            host=settings.host,
            port=settings.port,
            database=settings.name,
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


def is_unique_violation(exc: IntegrityError) -> bool:
    """判断 IntegrityError 是否由唯一约束冲突引起（覆盖三方言驱动文案）"""
    text = str(getattr(exc, "orig", None) or exc)
    return any(marker in text for marker in _UNIQUE_MARKERS)


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
        """创建新 ORM 会话；init_db 未完成时明确报错"""
        with self._lock:
            factory = self._factory
        if factory is None:
            raise RuntimeError("数据库尚未初始化：init_db 未完成")
        return factory()


_STATE = _EngineState()

# 请求级共享会话（由 app/api/deps.request_db_session 依赖注入，见其 docstring）。
# ContextVar 存 Session 本身：同一请求内的多次 DAO 调用复用同一会话，
# 省去逐 DAO 建会话/借还连接的开销；事务边界不变（仍由 get_db 块逐块提交）。
_REQUEST_SESSION: ContextVar[Optional[Session]] = ContextVar(
    "fn_request_session", default=None
)


def bind_request_session(session: Session) -> Token:
    """把会话绑定为当前上下文的请求级会话（FastAPI 依赖专用），返回复位令牌"""
    return _REQUEST_SESSION.set(session)


def unbind_request_session(token: Token) -> None:
    """复位请求级会话绑定（必须与 bind_request_session 配对，防跨请求串会话）"""
    _REQUEST_SESSION.reset(token)


def current_settings() -> DBSettings:
    """当前生效的连接配置（init_db 完成前回退到按优先级计算的配置）"""
    settings = _STATE.settings()
    if settings is None:
        return effective_db_settings()
    return settings


def new_session() -> Session:
    """创建新的 ORM 会话（init_db 未完成时抛错）；请求内会话复用见 api.deps"""
    return _STATE.new_session()


def current_engine() -> Engine:
    """当前生效的数据库引擎（init_db 未完成时抛错）"""
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
    """事务化 ORM 会话：正常结束提交，异常回滚，连接由引擎连接池管理

    请求上下文内（deps.request_db_session 已注入）复用请求级会话：提交/回滚
    语义与独立会话完全一致，仅不再重复建会话、借还连接；后台线程（调度器、
    手动触发任务）没有请求上下文，每次新建会话，行为与历史版本一致。
    """
    session = _REQUEST_SESSION.get()
    owned = session is None
    if owned:
        session = _STATE.new_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        if owned:
            session.close()


@contextmanager
def translate_unique_violation(message: str, code: int = ErrorCode.CONFLICT):
    """把作用域内抛出的唯一约束 IntegrityError 转为 ConflictError，其余异常原样传播

    DAO 写入方法用「预检查 + 唯一约束兜底」保证并发安全：并发下预检查可能漏过，
    数据库唯一约束是最终防线，冲突在此统一转成业务异常（服务层无需感知驱动差异）。
    """
    try:
        yield
    except IntegrityError as exc:
        if is_unique_violation(exc):
            raise ConflictError(message, code=code) from exc
        raise


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
