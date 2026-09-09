"""多类型数据库连接、方言适配与表结构初始化

通过向导参数支持 SQLite（默认）/ MySQL / PostgreSQL：
- 内部 SQL 统一使用 ? 占位符，由 Database 按方言转换为 %s
- INSERT 去重（OR IGNORE / IGNORE / ON CONFLICT）、自增主键回读等方言差异封装在本模块
- 月度统计统一使用 SUBSTR(tx_time, 1, 7)，三种方言行为一致
- 驱动按需导入：sqlite 无需驱动，mysql/postgresql 驱动由安装/升级/配置回调按向导选择装入 venv
"""
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Sequence

from app.config import (
    DB_HOST, DB_NAME, DB_PASSWORD, DB_PATH, DB_PORT, DB_TYPE, DB_USER,
    DEFAULT_CATEGORIES,
)


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
            cur = self.conn.cursor()
            cur.executemany(sql, seq)
            return cur.rowcount
        except Exception as exc:
            raise self._translate(exc) from exc

    def insert_ignore(self, table: str, cols: list[str], rows: Sequence[tuple]) -> None:
        """批量插入并在唯一键冲突时跳过该行"""
        collist = ", ".join(cols)
        ph = ", ".join("?" for _ in cols)
        if self.dialect == "mysql":
            sql = f"INSERT IGNORE INTO {table} ({collist}) VALUES ({ph})"
        elif self.dialect == "postgresql":
            sql = f"INSERT INTO {table} ({collist}) VALUES ({ph}) ON CONFLICT DO NOTHING"
        else:
            sql = f"INSERT OR IGNORE INTO {table} ({collist}) VALUES ({ph})"
        self.executemany(sql, list(rows))

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


@contextmanager
def get_db() -> Iterator[Database]:
    """事务化数据库会话：正常结束提交，异常回滚"""
    conn = _connect()
    db = Database(conn, DB_TYPE)
    try:
        yield db
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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


def init_db() -> None:
    """建表并预置默认分类，幂等可重复执行

    默认分类仅在分类表为空（首次安装）时预置，尊重用户对默认分类的删除/改名。
    """
    statements = _DDL_STATEMENTS.get(DB_TYPE)
    if statements is None:
        raise RuntimeError(f"不支持的数据库类型：{DB_TYPE}")
    with get_db() as db:
        for sql, ignore_errors in statements:
            try:
                db.execute(sql)
            except Exception:
                if not ignore_errors:
                    raise
        if db.query_one("SELECT COUNT(*) AS n FROM categories")["n"] == 0:
            db.insert_ignore("categories", ["name"], [(name,) for name in DEFAULT_CATEGORIES])
