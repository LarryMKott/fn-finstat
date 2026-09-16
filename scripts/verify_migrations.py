"""升级数据迁移机制验证（纯标准库 + 应用自身模块）

用法：app/venv/Scripts/python.exe scripts/verify_migrations.py

场景：
    A. 全新安装：建表、版本记为最新、预置分类；重复执行幂等
    B. 0.2.x 老库升级（无版本记录）：补记基线版本，应用真实 v1→v2 迁移（user_id 列），历史数据完整保留
    C. 模拟未来迁移（在当前最新版本之上加列）：迁移应用、SQLite 自动备份、断点续迁、缺迁移时拒绝启动
    D. 数据库类型切换：外部库方向明确告警不丢数据；跨库搬移逻辑单测（含 v1 旧库缺 user_id 列）

注意：_MIGRATIONS 定义在 app.db.migrations（不在 base），脚本通过
migrations._MIGRATIONS 注入模拟迁移；LATEST_SCHEMA_VERSION 为 base 模块
全局，init_db 直接读取 base.LATEST_SCHEMA_VERSION，故经 base 赋值即可生效。
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

# 沙箱环境变量必须先于 app 模块导入设置，使 DATA_DIR 指向临时目录
_SANDBOX = Path(tempfile.mkdtemp(prefix="fnfinstat-mig-"))
os.environ["TRIM_PKGVAR"] = str(_SANDBOX / "pkgvar")
os.environ["TRIM_PKGTMP"] = str(_SANDBOX / "pkgtmp")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402

from app.config import DATA_DIR, DB_PATH  # noqa: E402
from app.db import base, migrations  # noqa: E402
from app.db.copy import copy_database  # noqa: E402
from app.db.models import Base  # noqa: E402

PASS = 0
FAIL = 0

# 0.2.x 基线表结构（与老版本 DDL 一致，用于手工构造老库）
BILLS_DDL = """CREATE TABLE IF NOT EXISTS bills (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_time  TEXT    NOT NULL,
    account  TEXT    NOT NULL DEFAULT 'wechat',
    tx_type  TEXT    NOT NULL DEFAULT 'expense',
    merchant TEXT    NOT NULL DEFAULT '',
    amount   REAL    NOT NULL DEFAULT 0,
    category TEXT    NOT NULL DEFAULT '其他',
    tx_id    TEXT    UNIQUE,
    remark   TEXT    NOT NULL DEFAULT ''
)"""
CATEGORIES_DDL = """CREATE TABLE IF NOT EXISTS categories (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
)"""


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


def reset_sandbox() -> None:
    # 先释放活动引擎持有的连接（Windows 下未关闭的 sqlite 文件无法删除）
    engine = base._STATE.engine()
    if engine is not None:
        engine.dispose()
    for f in DATA_DIR.glob("*"):
        f.unlink(missing_ok=True)


def make_v1_old_db() -> None:
    """手工构造 0.2.x 老库：基线表结构 + 一条流水 + 一个分类，无 app_meta 版本记录"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(BILLS_DDL)
    conn.executescript(CATEGORIES_DDL)
    conn.execute(
        "INSERT INTO bills (tx_time, account, tx_type, merchant, amount, category, tx_id, remark)"
        " VALUES ('2026-01-01 12:00:00', 'wechat', 'expense', '老库商户', 9.9, '餐饮', 'OLD-1', '')"
    )
    conn.execute("INSERT INTO categories (name) VALUES ('餐饮')")
    conn.commit()
    conn.close()


def scenario_a_fresh():
    print("\n--- 场景 A：全新安装 ---")
    reset_sandbox()
    base.init_db()
    conn = sqlite3.connect(DB_PATH)
    version = conn.execute(
        "SELECT meta_value FROM app_meta WHERE meta_key = 'schema_version'"
    ).fetchone()
    n_cats = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    conn.close()
    check(
        "版本记为最新",
        version and int(version[0]) == base.LATEST_SCHEMA_VERSION,
        str(version),
    )
    check("预置分类 11 个", n_cats == 11, str(n_cats))
    base.init_db()
    conn = sqlite3.connect(DB_PATH)
    n_cats2 = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    conn.close()
    check("重复执行幂等（分类不重复）", n_cats2 == 11, str(n_cats2))


def scenario_b_old_db_upgrade():
    print("\n--- 场景 B：0.2.x 老库升级（真实 v1→v2 迁移）---")
    reset_sandbox()
    make_v1_old_db()
    base.init_db()
    conn = sqlite3.connect(DB_PATH)
    version = conn.execute(
        "SELECT meta_value FROM app_meta WHERE meta_key = 'schema_version'"
    ).fetchone()
    merchant = conn.execute(
        "SELECT merchant FROM bills WHERE tx_id = 'OLD-1'"
    ).fetchone()
    n_cats = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    user_id, has_idx = conn.execute(
        "SELECT user_id FROM bills WHERE tx_id = 'OLD-1'"
    ).fetchone(), bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'idx_bills_user_id'"
        ).fetchone()
    )
    conn.close()
    check(
        "老库补记为基线版本并迁移到最新",
        version and int(version[0]) == base.LATEST_SCHEMA_VERSION,
        str(version),
    )
    check(
        "历史流水保留且归入默认账号",
        merchant == ("老库商户",) and user_id == ("",),
        f"merchant={merchant}, user_id={user_id}",
    )
    check("user_id 索引已创建", has_idx)
    check("老库分类保留且不重复预置", n_cats == 1, str(n_cats))


def scenario_c_future_migration():
    print("\n--- 场景 C：模拟未来迁移 ---")
    real_latest = base.LATEST_SCHEMA_VERSION
    fake_key = (
        real_latest  # _MIGRATIONS 键 = 起始版本号（v{real_latest} → v{real_latest+1}）
    )
    reset_sandbox()
    make_v1_old_db()
    base.LATEST_SCHEMA_VERSION = real_latest + 1  # 先经全部真实迁移，再进模拟未来迁移
    migrations._MIGRATIONS[fake_key] = lambda session: session.execute(
        text("ALTER TABLE bills ADD COLUMN review_note TEXT NOT NULL DEFAULT ''")
    )
    try:
        base.init_db()
        conn = sqlite3.connect(DB_PATH)
        version = conn.execute(
            "SELECT meta_value FROM app_meta WHERE meta_key = 'schema_version'"
        ).fetchone()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bills)")}
        kept = conn.execute(
            "SELECT merchant, user_id FROM bills WHERE tx_id = 'OLD-1'"
        ).fetchone()
        conn.close()
        check(
            "版本迁移到模拟最新",
            version and int(version[0]) == real_latest + 1,
            str(version),
        )
        check("新列已添加", "review_note" in cols, str(cols))
        check("迁移后历史数据保留", kept == ("老库商户", ""), str(kept))
        backups = list(DATA_DIR.glob("bill.db.bak-v*"))
        check(
            "迁移前自动备份生成",
            len(backups) == 1 and backups[0].name == "bill.db.bak-v1",
            str([b.name for b in backups]),
        )
    finally:
        base.LATEST_SCHEMA_VERSION = real_latest
        migrations._MIGRATIONS.pop(fake_key, None)

    print("  --- 缺失迁移实现时拒绝启动 ---")
    base.LATEST_SCHEMA_VERSION = (
        real_latest + 2
    )  # v{real_latest+1} → v{real_latest+2} 无实现
    try:
        base.init_db()
        check("缺少迁移时抛 RuntimeError", False, "未抛出异常")
    except RuntimeError as exc:
        check("缺少迁移时抛 RuntimeError", "缺少" in str(exc), str(exc))
    finally:
        base.LATEST_SCHEMA_VERSION = real_latest

    base.init_db()  # 恢复到最新版本，供场景 D 使用


def scenario_d_db_type_switch():
    print("\n--- 场景 D：数据库类型切换 ---")
    # D1: 切到外部库方向（mysql）→ 明确告警，本地数据不受影响
    base._DB_TYPE_MARKER.write_text('{"db_type": "mysql"}', encoding="utf-8")
    base.init_db()
    conn = sqlite3.connect(DB_PATH)
    n = conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0]
    conn.close()
    marker = base.read_db_type_marker()
    check(
        "外部库方向仅告警且本地数据保留",
        n == 1 and marker == "sqlite",
        f"bills={n}, marker={marker}",
    )

    # D2: 跨库搬移逻辑单测（目标为空库时全量搬移并保留 id）
    src = DATA_DIR / "src-old.db"
    tgt = DATA_DIR / "tgt-new.db"
    for f in (src, tgt):
        f.unlink(missing_ok=True)
    scon = sqlite3.connect(src)
    scon.executescript(BILLS_DDL)
    scon.executescript(CATEGORIES_DDL)
    scon.execute(
        "INSERT INTO bills (tx_time, account, tx_type, merchant, amount, category, tx_id, remark)"
        " VALUES ('2026-02-02 08:00:00', 'alipay', 'income', '搬移商户', 66.6, '理财', 'MV-1', '')"
    )
    scon.execute("INSERT INTO categories (name) VALUES ('理财')")
    scon.commit()
    scon.close()
    src_engine = create_engine(f"sqlite:///{src.as_posix()}")
    tgt_engine = create_engine(f"sqlite:///{tgt.as_posix()}")
    Base.metadata.create_all(tgt_engine)
    try:
        stats = copy_database(src_engine, tgt_engine, base.LATEST_SCHEMA_VERSION)
        tcon = sqlite3.connect(tgt)
        got = tcon.execute(
            "SELECT merchant, amount FROM bills WHERE tx_id = 'MV-1'"
        ).fetchone()
        got_cat = tcon.execute(
            "SELECT COUNT(*) FROM categories WHERE name = '理财'"
        ).fetchone()[0]
        row = tcon.execute(
            "SELECT id, user_id FROM bills WHERE tx_id = 'MV-1'"
        ).fetchone()
        tcon.close()
        check("搬移流水 1 条", stats["copied_bills"] == 1, str(stats))
        check(
            "搬移分类 1 个",
            stats["copied_categories"] == 1 and got_cat == 1,
            f"moved={stats['copied_categories']}, in_tgt={got_cat}",
        )
        check("搬移数据完整", got == ("搬移商户", 66.6), str(got))
        check("空目标库保留源 id", row[0] == 1, str(row))
        check("v1 旧库缺 user_id 列 → 归入默认账号", row[1] == "", str(row))
    finally:
        src_engine.dispose()
        tgt_engine.dispose()


def main():
    print(f"=== 升级迁移机制验证（沙箱 {_SANDBOX}）===")
    scenario_a_fresh()
    scenario_b_old_db_upgrade()
    scenario_c_future_migration()
    scenario_d_db_type_switch()
    print(f"\n=== 结果：通过 {PASS} 项 / 失败 {FAIL} 项 ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
