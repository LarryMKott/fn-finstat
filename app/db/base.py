"""多类型数据库连接、方言适配、schema 版本迁移与表结构初始化

通过向导参数支持 SQLite（默认）/ MySQL / PostgreSQL：
- 内部 SQL 统一使用 ? 占位符，由 Database 按方言转换为 %s
- INSERT 去重（OR IGNORE / IGNORE / ON CONFLICT）、自增主键回读等方言差异封装在本模块
- 月度统计统一使用 SUBSTR(tx_time, 1, 7)，三种方言行为一致
- 驱动按需导入：sqlite 无需驱动，mysql/postgresql 驱动由安装/升级/配置回调按向导选择装入 venv

升级数据迁移（详见 init_db 与 _MIGRATIONS）：
- app_meta 表记录 schema_version；0.2.x 老库无此表，按基线版本补记后逐版本迁移
- 每个迁移独立事务、迁移后立即写版本戳，中断重启可从断点继续
- SQLite 在应用迁移前自动备份 bill.db；向导切换数据库类型时旧 SQLite 数据自动搬移
"""
import json
import logging
import shutil
import sqlite3
import threading
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

from app.config import (
    DB_HOST, DB_NAME, DB_PASSWORD, DB_PATH, DB_PORT, DB_TYPE, DB_USER,
    DATA_DIR, DEFAULT_CATEGORIES,
)

logger = logging.getLogger(__name__)

# 连接池容量：个人应用并发低，5 条足够；SQLite 复用单连接（线程安全由 WAL+busy_timeout 保证）
_POOL_MAX_SIZE = 5


class UniqueViolationError(Exception):
    """唯一约束冲突（各驱动统一转换为本异常，服务层转 400）"""


class Database:
    """最小方言适配封装：DAO 层只面向 ? 占位符与本类方法"""

    def __init__(self, conn, dialect: str):
        self.conn = conn
        self.dialect = dialect

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialect in ("mysql", "postgresql") else sql

    def _cursor(self):
        if self.dialect == "postgresql":
            import psycopg2.extras
            return self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return self.conn.cursor()  # sqlite: Row 工厂；mysql: DictCursor

    def _run(self, sql: str, params):
        try:
            return self._cursor().execute(self._sql(sql), params)
        except Exception as exc:
            raise self._translate(exc) from exc

    def _translate(self, exc: Exception) -> Exception:
        module = type(exc).__module__ or ""
        if "sqlite3" in module and "UNIQUE" in str(exc):
            return UniqueViolationError(str(exc))
        if "pymysql" in module and type(exc).__name__ == "IntegrityError":
            if getattr(exc, "args", None) and exc.args[0] == 1062:
                return UniqueViolationError(str(exc))
        if getattr(exc, "pgcode", None) == "23505":
            return UniqueViolationError(str(exc))
        return exc

    def query(self, sql: str, params=()) -> list[dict]:
        cur = self._run(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def query_one(self, sql: str, params=()) -> dict | None:
        cur = self._run(sql, params)
        row = cur.fetchone()
        return dict(row) if row is not None else None

    def execute(self, sql: str, params=()) -> int:
        return self._run(sql, params).rowcount

    def executemany(self, sql: str, seq: Sequence[tuple]) -> int:
        sql = self._sql(sql)
        try:
            cur = self._cursor()
            cur.executemany(sql, seq)
            return cur.rowcount
        except Exception as exc:
            raise self._translate(exc) from exc

    def insert_ignore(self, table: str, cols: list[str], rows: Sequence[tuple]) -> int:
        """批量插入并在唯一键冲突时跳过该行；返回实际新增条数（驱动不支持时返回 -1）"""
        collist = ", ".join(cols)
        ph = ", ".join("?" for _ in cols)
        if self.dialect == "mysql":
            sql = f"INSERT IGNORE INTO {table} ({collist}) VALUES ({ph})"
        elif self.dialect == "postgresql":
            sql = f"INSERT INTO {table} ({collist}) VALUES ({ph}) ON CONFLICT DO NOTHING"
        else:
            sql = f"INSERT OR IGNORE INTO {table} ({collist}) VALUES ({ph})"
        return self.executemany(sql, list(rows))

    def insert(self, table: str, data: dict) -> int:
        """插入一行并回读自增主键"""
        cols = list(data)
        ph = ", ".join("?" for _ in cols)
        collist = ", ".join(cols)
        params = tuple(data.values())
        if self.dialect == "postgresql":
            sql = f"INSERT INTO {table} ({collist}) VALUES ({ph}) RETURNING id"
            cur = self._run(sql, params)
            return cur.fetchone()["id"]
        sql = f"INSERT INTO {table} ({collist}) VALUES ({ph})"
        try:
            cur = self._cursor()
            cur.execute(self._sql(sql), params)
        except Exception as exc:
            raise self._translate(exc) from exc
        return cur.lastrowid


def _connect():
    """按配置建立连接；外部数据库驱动懒加载（未安装时给出明确指引）"""
    if DB_TYPE == "mysql":
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError(
                "未安装 MySQL 驱动：请在应用设置中确认数据库类型后重装应用，"
                "或手动执行 pip install PyMySQL cryptography"
            ) from exc
        return pymysql.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
            database=DB_NAME, charset="utf8mb4", autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
    if DB_TYPE == "postgresql":
        try:
            import psycopg2
        except ImportError as exc:
            raise RuntimeError(
                "未安装 PostgreSQL 驱动：请在应用设置中确认数据库类型后重装应用，"
                "或手动执行 pip install psycopg2-binary"
            ) from exc
        return psycopg2.connect(
            host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD, dbname=DB_NAME
        )
    if DB_TYPE == "sqlite":
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn
    raise RuntimeError(f"不支持的数据库类型：{DB_TYPE}")


class _ConnectionPool:
    """最小连接池：MySQL/PostgreSQL 复用连接，SQLite 复用单连接

    - acquire：池空且未达上限时新建；达上限时阻塞等待归还
    - release：归还连接（异常连接直接丢弃并新建）
    """

    def __init__(self, factory, max_size: int = _POOL_MAX_SIZE):
        self._factory = factory
        self._max_size = max_size
        self._pool: deque = deque()
        self._in_use = 0
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    def acquire(self):
        with self._cond:
            while True:
                if self._pool:
                    conn = self._pool.popleft()
                    self._in_use += 1
                    return conn
                if self._in_use < self._max_size:
                    self._in_use += 1
                    break
                self._cond.wait()
        # 新建连接放在锁外，避免持锁时进行网络 IO
        try:
            return self._factory()
        except Exception:
            with self._cond:
                self._in_use -= 1
                self._cond.notify()
            raise

    def release(self, conn, broken: bool = False):
        with self._cond:
            self._in_use -= 1
            if not broken and len(self._pool) < self._max_size:
                self._pool.append(conn)
            else:
                try:
                    conn.close()
                except Exception:
                    pass
            self._cond.notify()


# SQLite 复用单连接；MySQL/PostgreSQL 使用连接池
if DB_TYPE == "sqlite":
    _POOL = _ConnectionPool(_connect, max_size=1)
else:
    _POOL = _ConnectionPool(_connect, max_size=_POOL_MAX_SIZE)


@contextmanager
def get_db() -> Iterator[Database]:
    """事务化数据库会话：正常结束提交，异常回滚，连接归还池而非关闭"""
    conn = _POOL.acquire()
    db = Database(conn, DB_TYPE)
    broken = False
    try:
        yield db
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            broken = True
        raise
    finally:
        _POOL.release(conn, broken=broken)


# ---- 表结构（按方言）----
_DDL_STATEMENTS: dict[str, list[tuple[str, bool]]] = {
    # (SQL, 出错是否忽略) —— MySQL 不支持 CREATE INDEX IF NOT EXISTS，索引用忽略重复的方式建
    "sqlite": [
        ("""CREATE TABLE IF NOT EXISTS bills (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            tx_time  TEXT    NOT NULL,
            account  TEXT    NOT NULL DEFAULT 'wechat',
            tx_type  TEXT    NOT NULL DEFAULT 'expense',
            merchant TEXT    NOT NULL DEFAULT '',
            amount   REAL    NOT NULL DEFAULT 0,
            category TEXT    NOT NULL DEFAULT '其他',
            tx_id    TEXT    UNIQUE,
            remark   TEXT    NOT NULL DEFAULT ''
        )""", False),
        ("CREATE INDEX IF NOT EXISTS idx_bills_tx_time  ON bills(tx_time)", False),
        ("CREATE INDEX IF NOT EXISTS idx_bills_tx_type  ON bills(tx_type)", False),
        ("CREATE INDEX IF NOT EXISTS idx_bills_account  ON bills(account)", False),
        ("CREATE INDEX IF NOT EXISTS idx_bills_category ON bills(category)", False),
        ("""CREATE TABLE IF NOT EXISTS categories (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )""", False),
    ],
    "mysql": [
        ("""CREATE TABLE IF NOT EXISTS bills (
            id       BIGINT AUTO_INCREMENT PRIMARY KEY,
            tx_time  VARCHAR(32)  NOT NULL,
            account  VARCHAR(16)  NOT NULL DEFAULT 'wechat',
            tx_type  VARCHAR(16)  NOT NULL DEFAULT 'expense',
            merchant VARCHAR(256) NOT NULL DEFAULT '',
            amount   DOUBLE       NOT NULL DEFAULT 0,
            category VARCHAR(64)  NOT NULL DEFAULT '其他',
            tx_id    VARCHAR(64)  UNIQUE,
            remark   VARCHAR(512) NOT NULL DEFAULT ''
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""", False),
        ("CREATE INDEX idx_bills_tx_time  ON bills(tx_time)", True),
        ("CREATE INDEX idx_bills_tx_type  ON bills(tx_type)", True),
        ("CREATE INDEX idx_bills_account  ON bills(account)", True),
        ("CREATE INDEX idx_bills_category ON bills(category)", True),
        ("""CREATE TABLE IF NOT EXISTS categories (
            id   BIGINT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(64) UNIQUE NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""", False),
    ],
    "postgresql": [
        ("""CREATE TABLE IF NOT EXISTS bills (
            id       BIGSERIAL PRIMARY KEY,
            tx_time  VARCHAR(32)  NOT NULL,
            account  VARCHAR(16)  NOT NULL DEFAULT 'wechat',
            tx_type  VARCHAR(16)  NOT NULL DEFAULT 'expense',
            merchant VARCHAR(256) NOT NULL DEFAULT '',
            amount   DOUBLE PRECISION NOT NULL DEFAULT 0,
            category VARCHAR(64)  NOT NULL DEFAULT '其他',
            tx_id    VARCHAR(64)  UNIQUE,
            remark   VARCHAR(512) NOT NULL DEFAULT ''
        )""", False),
        ("CREATE INDEX IF NOT EXISTS idx_bills_tx_time  ON bills(tx_time)", False),
        ("CREATE INDEX IF NOT EXISTS idx_bills_tx_type  ON bills(tx_type)", False),
        ("CREATE INDEX IF NOT EXISTS idx_bills_account  ON bills(account)", False),
        ("CREATE INDEX IF NOT EXISTS idx_bills_category ON bills(category)", False),
        ("""CREATE TABLE IF NOT EXISTS categories (
            id   BIGSERIAL PRIMARY KEY,
            name VARCHAR(64) UNIQUE NOT NULL
        )""", False),
    ],
}


# bills 业务列（不含自增 id）：建表 DDL、批量导入与跨库搬移共用同一列序
BILL_DATA_COLS = ["tx_time", "account", "tx_type", "merchant", "amount", "category", "tx_id", "remark"]

# ---- Schema 版本与升级迁移 ----
# - app_meta 表记录 schema_version（键值结构，方言无关）
# - _MIGRATIONS 登记从 vN → vN+1 的迁移函数（签名 fn(db: Database)），启动时按序应用
# - 每个迁移独立事务、迁移完成后立即写版本戳：中断重启自动从断点继续
# - 注意：MySQL 的 DDL 会隐式提交无法回滚，迁移函数应写成幂等（可重复执行）
BASELINE_SCHEMA_VERSION = 1  # 0.2.x 建表即该版本（bills + categories），无版本记录的老库按此补记
LATEST_SCHEMA_VERSION = 1
_SCHEMA_VERSION_KEY = "schema_version"

_META_TABLE_DDL = {
    "sqlite": "CREATE TABLE IF NOT EXISTS app_meta (meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL)",
    "mysql": (
        "CREATE TABLE IF NOT EXISTS app_meta ("
        "meta_key VARCHAR(64) PRIMARY KEY, meta_value VARCHAR(255) NOT NULL"
        ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    ),
    "postgresql": "CREATE TABLE IF NOT EXISTS app_meta (meta_key VARCHAR(64) PRIMARY KEY, meta_value VARCHAR(255) NOT NULL)",
}

# 迁移注册表：key 为起始版本。发布 schema 变更时按以下步骤登记，并把 LATEST_SCHEMA_VERSION +1：
#   def _v1_add_created_at(db: "Database") -> None:
#       # 三方言 ALTER 语法不同时按 db.dialect 分支；MySQL 下 DDL 隐式提交，需保证可重复执行
#       db.execute({
#           "sqlite":     "ALTER TABLE bills ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
#           "mysql":      "ALTER TABLE bills ADD COLUMN created_at VARCHAR(32) NOT NULL DEFAULT ''",
#           "postgresql": "ALTER TABLE bills ADD COLUMN created_at VARCHAR(32) NOT NULL DEFAULT ''",
#       }[db.dialect])
#   _MIGRATIONS[1] = _v1_add_created_at
_MIGRATIONS: dict[int, Callable[["Database"], None]] = {}


def _table_exists(db: "Database", table: str) -> bool:
    if db.dialect == "sqlite":
        row = db.query_one("SELECT 1 AS one FROM sqlite_master WHERE type = 'table' AND name = ?", (table,))
    elif db.dialect == "mysql":
        row = db.query_one(
            "SELECT 1 AS one FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = ?",
            (table,),
        )
    else:
        row = db.query_one("SELECT to_regclass(?) AS reg", (table,))
        return row is not None and row["reg"] is not None
    return row is not None


def _get_schema_version(db: "Database") -> Optional[int]:
    row = db.query_one("SELECT meta_value AS v FROM app_meta WHERE meta_key = ?", (_SCHEMA_VERSION_KEY,))
    return int(row["v"]) if row else None


def _set_schema_version(db: "Database", version: int) -> None:
    db.execute("DELETE FROM app_meta WHERE meta_key = ?", (_SCHEMA_VERSION_KEY,))
    db.execute(
        "INSERT INTO app_meta (meta_key, meta_value) VALUES (?, ?)", (_SCHEMA_VERSION_KEY, str(version))
    )


def _sqlite_backup_if_pending() -> None:
    """SQLite 专有：存在待应用迁移时，先 checkpoint 并备份 bill.db（迁移前的安全快照）

    无版本记录的老库（0.2.x）也按基线版本对待——补记版本号后同样可能进入迁移流程，需备份。
    """
    if DB_TYPE != "sqlite" or not DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(_META_TABLE_DDL["sqlite"])
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


# ---- 数据库类型切换（向导改配置后重启触发）----
# 标记文件记录上次使用的数据库类型，与数据库本身解耦（切换后旧库可能无法再连接）
_DB_TYPE_MARKER = DATA_DIR / "db_meta.json"


def _read_db_type_marker() -> Optional[str]:
    try:
        return json.loads(_DB_TYPE_MARKER.read_text(encoding="utf-8")).get("db_type")
    except FileNotFoundError:
        return None
    except Exception:
        logger.warning("数据库类型标记文件损坏，按首次安装处理：%s", _DB_TYPE_MARKER)
        return None


def _write_db_type_marker() -> None:
    try:
        _DB_TYPE_MARKER.write_text(json.dumps({"db_type": DB_TYPE}), encoding="utf-8")
    except Exception:
        logger.warning("写入数据库类型标记失败（不影响运行）：%s", _DB_TYPE_MARKER)


def _copy_sqlite_data_into(db: "Database", source: Path) -> tuple[int, int]:
    """把旧 SQLite 库的 bills/categories 搬移到当前活动数据库（仅目标为空时调用）

    返回 (搬移流水数, 搬移分类数)。列取两库交集，兼容旧源库缺新列的情况。
    """
    src = sqlite3.connect(source)
    src.row_factory = sqlite3.Row
    try:
        src_cols = {r[1] for r in src.execute("PRAGMA table_info(bills)")}
        bills = [dict(r) for r in src.execute("SELECT * FROM bills")]
        has_cats = src.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'categories'"
        ).fetchone()
        cats = [dict(r) for r in src.execute("SELECT * FROM categories")] if has_cats else []
    finally:
        src.close()
    cols = [c for c in BILL_DATA_COLS if c in src_cols]
    inserted = db.insert_ignore("bills", cols, [tuple(r[c] for c in cols) for r in bills])
    db.insert_ignore("categories", ["name"], [(r["name"],) for r in cats])
    if inserted < 0:  # 驱动 rowcount 不可靠时以源行数为准
        inserted = len(bills)
    return inserted, len(cats)


def _handle_db_type_switch(previous: str) -> None:
    """数据库类型变更时的数据迁移：旧库为 SQLite 则自动搬移，其余方向明确告警

    - 仅当新库为空时搬移（避免两库数据混杂）；旧 bill.db 文件保留不动，可随时回退
    - 旧库为外部数据库时连接参数已随向导失效，无法自动迁移，提示用户处理
    """
    logger.warning("检测到数据库类型变更：%s -> %s", previous, DB_TYPE)
    if previous == DB_TYPE:
        return
    if previous != "sqlite":
        logger.warning(
            "旧 %s 库中的数据无法自动迁移（历史连接参数已失效）。"
            "数据仍保留在原数据库中，如需找回请恢复原向导配置或使用数据库工具手动导出。", previous,
        )
        return
    if not DB_PATH.exists():
        return
    with get_db() as db:
        has_data = db.query_one("SELECT COUNT(*) AS n FROM bills")["n"] > 0 or \
            db.query_one("SELECT COUNT(*) AS n FROM categories")["n"] > 0
        if has_data:
            logger.warning("新数据库非空，跳过自动搬移以避免混入两库数据；旧数据仍保留在 %s", DB_PATH)
            return
        moved_bills, moved_cats = _copy_sqlite_data_into(db, DB_PATH)
    logger.info("已从旧 SQLite 库搬移 %s 条流水、%s 个分类（源文件保留于 %s）", moved_bills, moved_cats, DB_PATH)


def init_db() -> None:
    """建表、应用 schema 迁移并预置默认分类，幂等可重复执行

    版本判定：
        app_meta 有记录  → 以记录为准，逐版本应用 _MIGRATIONS 至最新
        无记录但有 bills 表（0.2.x 老库升级）→ 按基线版本补记后照常迁移
        无记录且无表（首次安装）→ 建表后直接记为最新版本
    默认分类仅在分类表为空时预置，尊重用户对默认分类的删除/改名。
    """
    statements = _DDL_STATEMENTS.get(DB_TYPE)
    if statements is None:
        raise RuntimeError(f"不支持的数据库类型：{DB_TYPE}")
    meta_ddl = _META_TABLE_DDL[DB_TYPE]

    _sqlite_backup_if_pending()

    # 基础表结构幂等执行（保持历史行为：每次启动修复缺失表/索引）
    with get_db() as db:
        db.execute(meta_ddl)
        had_bills = _table_exists(db, "bills")
        for sql, ignore_errors in statements:
            try:
                db.execute(sql)
            except Exception:
                if not ignore_errors:
                    raise
        current = _get_schema_version(db)
        if current is None:
            current = BASELINE_SCHEMA_VERSION if had_bills else LATEST_SCHEMA_VERSION
            _set_schema_version(db, current)
            if had_bills:
                logger.info("检测到 v0.2.x 老库，schema 版本补记为 v%d", current)

    # 逐版本应用迁移（每个迁移独立事务，失败时已完成的版本保留，重启续迁）
    while current < LATEST_SCHEMA_VERSION:
        migrate = _MIGRATIONS.get(current)
        if migrate is None:
            raise RuntimeError(f"数据库升级缺少 v{current} → v{current + 1} 的迁移实现，已停止启动以保护数据")
        with get_db() as db:
            migrate(db)
            _set_schema_version(db, current + 1)
        logger.info("数据库 schema 已从 v%d 迁移到 v%d", current, current + 1)
        current += 1

    previous_type = _read_db_type_marker()
    if previous_type is not None and previous_type != DB_TYPE:
        _handle_db_type_switch(previous_type)

    with get_db() as db:
        if db.query_one("SELECT COUNT(*) AS n FROM categories")["n"] == 0:
            db.insert_ignore("categories", ["name"], [(name,) for name in DEFAULT_CATEGORIES])
    _write_db_type_marker()
    logger.info("数据库就绪（%s，schema v%d）", DB_TYPE, current)
