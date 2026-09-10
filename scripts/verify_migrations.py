"""升级数据迁移机制验证（纯标准库 + 应用自身模块）

用法：app/venv/Scripts/python.exe scripts/verify_migrations.py

场景：
    A. 全新安装：建表、版本记为最新、预置分类；重复执行幂等
    B. 0.2.x 老库升级（无版本记录）：补记基线版本，历史数据完整保留
    C. 模拟未来迁移（v1 → v2 加列）：迁移应用、SQLite 自动备份、断点续迁、缺迁移时拒绝启动
    D. 数据库类型切换：外部库方向明确告警不丢数据；SQLite 搬移逻辑单测
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

from app.config import DATA_DIR, DB_PATH  # noqa: E402
from app.db import base  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {extra}")


def reset_sandbox() -> None:
    # 先关闭连接池持有的连接（Windows 下未关闭的 sqlite 文件无法删除）
    with base._POOL._cond:
        while base._POOL._pool:
            base._POOL._pool.popleft().close()
    for f in DATA_DIR.glob("*"):
        f.unlink(missing_ok=True)


def make_v1_old_db() -> None:
    """手工构造 0.2.x 老库：基线表结构 + 一条流水 + 一个分类，无 app_meta 版本记录"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(base._DDL_STATEMENTS["sqlite"][0][0])  # bills 建表
    conn.executescript(base._DDL_STATEMENTS["sqlite"][5][0])  # categories 建表
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
    version = conn.execute("SELECT meta_value FROM app_meta WHERE meta_key = 'schema_version'").fetchone()
    n_cats = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    conn.close()
    check("版本记为最新", version and int(version[0]) == base.LATEST_SCHEMA_VERSION, str(version))
    check("预置分类 11 个", n_cats == 11, str(n_cats))
    base.init_db()
    conn = sqlite3.connect(DB_PATH)
    n_cats2 = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    conn.close()
    check("重复执行幂等（分类不重复）", n_cats2 == 11, str(n_cats2))


def scenario_b_old_db_upgrade():
    print("\n--- 场景 B：0.2.x 老库升级 ---")
    reset_sandbox()
    make_v1_old_db()
    base.init_db()
    conn = sqlite3.connect(DB_PATH)
    version = conn.execute("SELECT meta_value FROM app_meta WHERE meta_key = 'schema_version'").fetchone()
    merchant = conn.execute("SELECT merchant FROM bills WHERE tx_id = 'OLD-1'").fetchone()
    n_cats = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    conn.close()
    check("老库补记为基线版本", version and int(version[0]) == base.BASELINE_SCHEMA_VERSION, str(version))
    check("历史流水保留", merchant == ("老库商户",), str(merchant))
    check("老库分类保留且不重复预置", n_cats == 1, str(n_cats))


def scenario_c_future_migration():
    print("\n--- 场景 C：模拟未来迁移 v1 → v2 ---")
    reset_sandbox()
    make_v1_old_db()
    base.LATEST_SCHEMA_VERSION = 2
    base._MIGRATIONS[1] = lambda db: db.execute(
        "ALTER TABLE bills ADD COLUMN review_note TEXT NOT NULL DEFAULT ''"
    )
    try:
        base.init_db()
        conn = sqlite3.connect(DB_PATH)
        version = conn.execute("SELECT meta_value FROM app_meta WHERE meta_key = 'schema_version'").fetchone()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bills)")}
        kept = conn.execute("SELECT merchant FROM bills WHERE tx_id = 'OLD-1'").fetchone()
        conn.close()
        check("版本迁移到 v2", version and int(version[0]) == 2, str(version))
        check("新列已添加", "review_note" in cols, str(cols))
        check("迁移后历史数据保留", kept == ("老库商户",), str(kept))
        backups = list(DATA_DIR.glob("bill.db.bak-v*"))
        check("迁移前自动备份生成", len(backups) == 1 and backups[0].name == "bill.db.bak-v1",
              str([b.name for b in backups]))
    finally:
        base.LATEST_SCHEMA_VERSION = 1
        base._MIGRATIONS.clear()

    print("  --- 缺失迁移实现时拒绝启动 ---")
    base.LATEST_SCHEMA_VERSION = 3  # v2 → v3 无实现
    try:
        base.init_db()
        check("缺少迁移时抛 RuntimeError", False, "未抛出异常")
    except RuntimeError as exc:
        check("缺少迁移时抛 RuntimeError", "缺少 v2" in str(exc), str(exc))
    finally:
        base.LATEST_SCHEMA_VERSION = 1

    base.init_db()  # 恢复到最新版本，供场景 D 使用


def scenario_d_db_type_switch():
    print("\n--- 场景 D：数据库类型切换 ---")
    # D1: 切到外部库方向（mysql）→ 明确告警，本地数据不受影响
    (base._DB_TYPE_MARKER).write_text('{"db_type": "mysql"}', encoding="utf-8")
    base.init_db()
    conn = sqlite3.connect(DB_PATH)
    n = conn.execute("SELECT COUNT(*) FROM bills").fetchone()[0]
    conn.close()
    marker = base._read_db_type_marker()
    check("外部库方向仅告警且本地数据保留", n == 1 and marker == "sqlite", f"bills={n}, marker={marker}")

    # D2: SQLite 搬移逻辑单测（目标为空库时全量搬移，列取交集）
    src = DATA_DIR / "src-old.db"
    tgt = DATA_DIR / "tgt-new.db"
    for f in (src, tgt):
        f.unlink(missing_ok=True)
    scon = sqlite3.connect(src)
    scon.executescript(base._DDL_STATEMENTS["sqlite"][0][0])
    scon.executescript(base._DDL_STATEMENTS["sqlite"][5][0])
    scon.execute(
        "INSERT INTO bills (tx_time, account, tx_type, merchant, amount, category, tx_id, remark)"
        " VALUES ('2026-02-02 08:00:00', 'alipay', 'income', '搬移商户', 66.6, '理财', 'MV-1', '')"
    )
    scon.execute("INSERT INTO categories (name) VALUES ('理财')")
    scon.commit()
    scon.close()
    tcon = sqlite3.connect(tgt)
    tcon.executescript(base._DDL_STATEMENTS["sqlite"][0][0])
    tcon.executescript(base._DDL_STATEMENTS["sqlite"][5][0])
    moved_bills, moved_cats = base._copy_sqlite_data_into(base.Database(tcon, "sqlite"), src)
    got = tcon.execute("SELECT merchant, amount FROM bills WHERE tx_id = 'MV-1'").fetchone()
    got_cat = tcon.execute("SELECT COUNT(*) FROM categories WHERE name = '理财'").fetchone()[0]
    tcon.close()
    check("搬移流水 1 条", moved_bills == 1, str(moved_bills))
    check("搬移分类 1 个", moved_cats == 1 and got_cat == 1, f"moved={moved_cats}, in_tgt={got_cat}")
    check("搬移数据完整", got == ("搬移商户", 66.6), str(got))


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
