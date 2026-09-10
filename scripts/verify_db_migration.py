"""设置页「迁移并切换」机制验证（沙箱运行，不影响本地开发数据）

用法：app/venv/Scripts/python.exe scripts/verify_db_migration.py

场景：
    A. 去重插入语句的三方言编译检查（INSERT IGNORE / ON CONFLICT / OR IGNORE）
    B. 运行期切换全流程：旧库导入数据 → 迁移到新库 → 引擎即时切换 → 新库可读写
    B2. 多账号隔离：不同飞牛账号的流水互不可见；认领历史数据归入当前账号
    C. 新库去重合并：向已有数据的目标库二次迁移，重复流水被跳过
    D. 连接配置优先级：向导显式环境变量 > db_config.json（设置页写入）> 通用环境变量
"""
import os
import sys
import tempfile
from pathlib import Path

# 沙箱环境变量必须先于 app 模块导入设置
_SANDBOX = Path(tempfile.mkdtemp(prefix="fnfinstat-mig2-"))
os.environ["TRIM_PKGVAR"] = str(_SANDBOX / "pkgvar")
os.environ["TRIM_PKGTMP"] = str(_SANDBOX / "pkgtmp")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.dialects import mysql, postgresql, sqlite  # noqa: E402

from app.config import DB_CONFIG_FILE, DATA_DIR, effective_db_settings, write_db_config_file  # noqa: E402
from app.db import base  # noqa: E402
from app.db.copy import copy_database  # noqa: E402
from app.db.dao.bill_dao import BillDAO  # noqa: E402
from app.db.dao.category_dao import CategoryDAO  # noqa: E402
from app.db.models import Base, Bill, Category  # noqa: E402

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


def _compile_insert_ignore(dialect) -> str:
    table = Bill.__table__
    if dialect.name == "mysql":
        from sqlalchemy.dialects.mysql import insert
        stmt = insert(table).prefix_with("IGNORE")
    elif dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
        stmt = insert(table).on_conflict_do_nothing()
    else:
        from sqlalchemy.dialects.sqlite import insert
        stmt = insert(table).prefix_with("OR IGNORE")
    return str(stmt.compile(dialect=dialect))


def scenario_a_dialect_sql():
    print("\n--- 场景 A：三方言去重插入语句编译 ---")
    mysql_sql = _compile_insert_ignore(mysql.dialect())
    pg_sql = _compile_insert_ignore(postgresql.dialect())
    sqlite_sql = _compile_insert_ignore(sqlite.dialect())
    check("MySQL: INSERT IGNORE", "INSERT IGNORE INTO bills" in mysql_sql, mysql_sql)
    check("PG: ON CONFLICT DO NOTHING", "ON CONFLICT DO NOTHING" in pg_sql, pg_sql)
    check("SQLite: INSERT OR IGNORE", "INSERT OR IGNORE INTO bills" in sqlite_sql, sqlite_sql)


_RECORDS = [
    {"tx_time": "2026-09-01 10:00:00", "account": "wechat", "tx_type": "expense",
     "merchant": "迁移测试商户", "amount": 12.5, "category": "餐饮", "tx_id": "MIG-1", "remark": ""},
    {"tx_time": "2026-09-02 11:00:00", "account": "alipay", "tx_type": "income",
     "merchant": "", "amount": 99.0, "category": "理财", "tx_id": "MIG-2", "remark": ""},
]


def _seed_source() -> None:
    base.init_db()
    CategoryDAO.ensure_many([r["category"] for r in _RECORDS])
    assert BillDAO.insert_many(_RECORDS, user_id="") == 2


def _migrate_to(target_file: Path) -> dict:
    """复刻 settings_service.migrate_and_switch 的核心步骤（目标为本地文件库）"""
    from app.config import DBSettings

    target = create_engine(f"sqlite:///{target_file.as_posix()}")
    Base.metadata.create_all(target)
    stats = copy_database(base.current_engine(), target, base.LATEST_SCHEMA_VERSION)
    base.activate_engine(DBSettings(), target)  # sqlite 本地文件库仅用于机制验证
    base.write_db_type_marker(DBSettings())
    return stats


def scenario_b_switch():
    print("\n--- 场景 B：迁移并即时切换 ---")
    for f in DATA_DIR.glob("*"):
        f.unlink(missing_ok=True)
    _seed_source()
    target_file = DATA_DIR / "target.db"
    stats = _migrate_to(target_file)
    check("源数据统计正确（含预置分类）",
          stats["source_bills"] == 2 and stats["source_categories"] == 12, str(stats))
    check("目标为空 → 全量搬移", stats["copied_bills"] == 2 and not stats["target_had_data"], str(stats))
    check("切换后查询到搬移数据", BillDAO.tx_id_exists("MIG-1"))
    # 新库可正常写入：自增 id 不与搬移的 id 冲突
    new_id = BillDAO.create(
        {**_RECORDS[0], "tx_id": "MIG-3", "tx_time": "2026-09-03 09:00:00"}, user_id=""
    )
    got = BillDAO.get_by_id(new_id, user_id="")
    check("切换后可写入新流水", got is not None and got["tx_id"] == "MIG-3", str(got))
    import sqlite3

    old = sqlite3.connect(DATA_DIR / "bill.db")
    n_old = old.execute("SELECT COUNT(*) FROM bills WHERE tx_id = 'MIG-3'").fetchone()[0]
    old.close()
    check("旧库只读未被写入", n_old == 0, str(n_old))


def scenario_b2_multi_user():
    print("\n--- 场景 B2：多账号数据隔离 ---")
    BillDAO.create({**_RECORDS[0], "tx_id": "U-A-1"}, user_id="100")
    BillDAO.create({**_RECORDS[0], "tx_id": "U-B-1"}, user_id="200")
    check("账号 100 看不到账号 200 的流水", BillDAO.get_by_id(5, "100") is None)
    check("账号 200 看不到账号 100 的流水", BillDAO.get_by_id(4, "200") is None)
    total_a, _ = BillDAO.list_bills("100", page=1, page_size=10)
    total_b, _ = BillDAO.list_bills("200", page=1, page_size=10)
    check("各账号流水数独立", total_a == 1 and total_b == 1, f"a={total_a}, b={total_b}")
    default_total, _ = BillDAO.list_bills("", page=1, page_size=50)
    check("认领历史数据归入当前账号", BillDAO.claim_unassigned("100") == default_total == 3,
          f"default={default_total}")
    total_100, _ = BillDAO.list_bills("100", page=1, page_size=50)
    check("认领后账号 100 流水数", total_100 == 4, str(total_100))


def scenario_c_merge():
    print("\n--- 场景 C：目标非空 → 去重合并 ---")
    target = create_engine(f"sqlite:///{(DATA_DIR / 'target.db').as_posix()}")
    stats = copy_database(base.current_engine(), target, base.LATEST_SCHEMA_VERSION)
    check("重复流水被跳过", stats["copied_bills"] == 0, str(stats))
    check("标记为目标有数据(合并)", stats["target_had_data"], str(stats))
    target.dispose()


def _write_file_config() -> None:
    DB_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_db_config_file(_FakeMysql("10.0.0.8", 3307, "fndb", "fnuser", "secret"))


class _FakeMysql:
    """write_db_config_file 只读属性鸭子类型，避免构造真实 DBSettings 的耦合"""

    def __init__(self, host, port, name, user, password):
        self.db_type, self.host, self.port = "mysql", host, port
        self.name, self.user, self.password = name, user, password


def scenario_d_config_priority():
    print("\n--- 场景 D：连接配置优先级 ---")
    for f in DATA_DIR.glob("*"):
        f.unlink(missing_ok=True)
    _write_file_config()
    s = effective_db_settings()
    check("设置页写入的配置生效", s.db_type == "mysql" and s.host == "10.0.0.8"
          and s.port == 3307 and s.password == "secret", str(s))

    # 通用环境变量不应压过设置页配置
    os.environ["DB_HOST"] = "192.168.1.1"
    s = effective_db_settings()
    check("通用环境变量不影响设置页配置", s.host == "10.0.0.8", s.host)
    del os.environ["DB_HOST"]

    # 向导显式变量优先级最高
    os.environ["wizard_db_host"] = "172.16.0.1"
    os.environ["wizard_db_type"] = "postgresql"
    s = effective_db_settings()
    check("向导显式变量优先", s.db_type == "postgresql" and s.host == "172.16.0.1", str(s))
    del os.environ["wizard_db_host"], os.environ["wizard_db_type"]


def main():
    print(f"=== 迁移并切换机制验证（沙箱 {_SANDBOX}）===")
    scenario_a_dialect_sql()
    scenario_b_switch()
    scenario_b2_multi_user()
    scenario_c_merge()
    engine = base._STATE.engine()
    if engine is not None:
        engine.dispose()
    scenario_d_config_priority()
    print(f"\n=== 结果：通过 {PASS} 项 / 失败 {FAIL} 项 ===")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
