"""设置服务测试：历史数据认领与「迁移并切换」流程（以临时 SQLite 充当目标库）

TargetDatabase 仅接受 mysql/postgresql，为避免外部依赖，测试中把 build_engine
重定向到临时 SQLite 引擎，只验证迁移编排逻辑本身。
"""

import pytest

from app.api.deps import GatewayUser
from app.db.base import _STATE
from app.db.dao.bill_dao import BillDAO
from app.db.drivers import ensure_driver
from app.schemas.settings import TargetDatabase
from app.services import settings_service
from tests.conftest import USER_A, make_bill_records


def test_ensure_driver_sqlite_noop():
    ensure_driver("sqlite")  # sqlite 无需驱动，不应抛异常


def test_ensure_driver_missing_mysql_raises(monkeypatch):
    monkeypatch.setattr("app.db.drivers._importable", lambda module: False)
    monkeypatch.setattr(
        "app.db.drivers.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no pip")),
    )
    with pytest.raises(RuntimeError) as e:
        ensure_driver("mysql")
    assert "自动安装失败" in str(e.value)


def test_claim_legacy_bills(db):
    BillDAO.insert_many(make_bill_records(2), "")  # 升级前无归属数据
    result = settings_service.claim_legacy_bills(
        GatewayUser(user_id=USER_A, user_name="张三")
    )
    assert result.claimed == 2
    assert "2" in result.message
    total, _ = BillDAO.list_bills(USER_A)
    assert total == 2

    # 再次认领：无数据可领
    again = settings_service.claim_legacy_bills(GatewayUser(user_id=USER_A))
    assert again.claimed == 0


def test_get_database_info(db):
    BillDAO.insert_many(make_bill_records(3), USER_A)
    BillDAO.insert_many(make_bill_records(1, prefix="T7"), "")
    info = settings_service.get_database_info(
        GatewayUser(user_id=USER_A, user_name="张三")
    )
    assert info.db_type == "sqlite"
    assert info.bills == 3
    assert info.categories > 0
    assert info.schema_version == info.schema_latest == 2
    assert info.user_id == USER_A
    assert info.user_name == "张三"
    assert info.unassigned_bills == 1
    assert info.sqlite_path.endswith("bill.db")


def test_migrate_and_switch_to_new_database(db, tmp_path, monkeypatch):
    BillDAO.insert_many(make_bill_records(2, category="餐饮"), USER_A)
    BillDAO.insert_many(make_bill_records(1, prefix="T8", category="购物"), "")

    # 目标库与持久化文件、类型标记都指向临时目录
    monkeypatch.setattr("app.config.DB_CONFIG_FILE", tmp_path / "db_config.json")
    monkeypatch.setattr("app.db.base._DB_TYPE_MARKER", tmp_path / "db_meta.json")
    monkeypatch.setattr(
        "app.services.settings_service.ensure_driver", lambda db_type: None
    )

    target_engine_holder = {}
    from tests.conftest import make_engine

    def fake_build_engine(settings):
        engine = make_engine(tmp_path / "target.db")
        target_engine_holder["engine"] = engine
        return engine

    monkeypatch.setattr("app.services.settings_service.build_engine", fake_build_engine)

    result = settings_service.migrate_and_switch(
        TargetDatabase(
            db_type="mysql", host="ignored", name="target", user="u", password="p"
        )
    )
    assert result.source_bills == 3
    assert result.copied_bills == 3
    assert result.copied_categories == result.source_categories
    assert result.merged is False
    assert result.switched is True

    # 运行期已切换到目标引擎，数据完整、无归属数据一并搬移
    assert _STATE.engine() is target_engine_holder["engine"]
    total, rows = BillDAO.list_bills(USER_A)
    assert (total, len(rows)) == (2, 2)
    assert BillDAO.count_unassigned() == 1

    # 连接配置持久化（重启后仍指向新库）且密码不回传到 info
    import json

    saved = json.loads((tmp_path / "db_config.json").read_text(encoding="utf-8"))
    assert saved["db_type"] == "mysql"
    assert saved["password"] == "p"

    target_engine_holder["engine"].dispose()


def test_test_target_connection_reports_failure(db, monkeypatch, tmp_path):
    """连通性测试失败返回 ok=False 而不抛异常"""
    monkeypatch.setattr(
        "app.services.settings_service.ensure_driver", lambda db_type: None
    )
    from tests.conftest import make_engine

    monkeypatch.setattr(
        "app.services.settings_service.build_engine",
        lambda settings: make_engine(
            tmp_path / "probe.db"
        ),  # sqlite 没有 version() 函数
    )
    result = settings_service.test_target_connection(
        TargetDatabase(db_type="postgresql", name="x")
    )
    assert result.ok is False
    assert result.message
