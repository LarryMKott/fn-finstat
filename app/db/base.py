"""SQLite 数据库连接与表结构初始化"""
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.config import DB_PATH, DEFAULT_CATEGORIES


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """事务化数据库会话：正常结束提交，异常回滚"""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """建表并预置默认分类，幂等可重复执行"""
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bills (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_time  TEXT    NOT NULL,
                account  TEXT    NOT NULL DEFAULT 'wechat',
                tx_type  TEXT    NOT NULL DEFAULT 'expense',
                merchant TEXT    NOT NULL DEFAULT '',
                amount   REAL    NOT NULL DEFAULT 0,
                category TEXT    NOT NULL DEFAULT '其他',
                tx_id    TEXT    UNIQUE,
                remark   TEXT    NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_bills_tx_time  ON bills(tx_time);
            CREATE INDEX IF NOT EXISTS idx_bills_tx_type  ON bills(tx_type);
            CREATE INDEX IF NOT EXISTS idx_bills_account  ON bills(account);
            CREATE INDEX IF NOT EXISTS idx_bills_category ON bills(category);

            CREATE TABLE IF NOT EXISTS categories (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );
            """
        )
        for name in DEFAULT_CATEGORIES:
            conn.execute("INSERT OR IGNORE INTO categories(name) VALUES (?)", (name,))
