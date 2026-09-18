"""账本维度测试（T-7.1）：默认账本、账本 CRUD、数据归属与隔离、接口权限

覆盖开发计划 T-7.1 的两条行为底线：
- 不传 ledger_id 的旧调用行为不变（写落默认账本、读不按账本过滤）
- 删除账本不等于删除数据（流水/预算/快照并入默认账本）
"""

import pytest

from app.core.errors import ConflictError, ValidationError
from app.db.dao.asset_dao import AssetDAO
from app.db.dao.bill_dao import BillDAO
from app.db.dao.budget_dao import BudgetDAO
from app.db.dao.ledger_dao import LedgerDAO
from app.db.models import DEFAULT_LEDGER_ID
from app.services import ledger_service
from tests.conftest import USER_A, make_bill_records

A_HEADERS = {"X-Trim-Userid": USER_A}
ADMIN_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Isadmin": "true"}


def test_default_ledger_seeded(db):
    """全新库建表后默认账本即存在（与 init_db 行为一致）"""
    ledgers = LedgerDAO.list_with_counts()
    assert len(ledgers) == 1
    assert ledgers[0]["name"] == "默认账本"
    assert ledgers[0]["is_default"] is True
    assert ledgers[0]["bill_count"] == 0


def test_create_and_list(db):
    other = LedgerDAO.create("装修账本")
    assert other["is_default"] is False
    ledgers = LedgerDAO.list_with_counts()
    assert [l["name"] for l in ledgers] == ["默认账本", "装修账本"]  # 默认账本排最前


def test_create_duplicate_name_conflict(db):
    LedgerDAO.create("装修账本")
    with pytest.raises(ConflictError):
        LedgerDAO.create("装修账本")


def test_write_without_ledger_id_lands_on_default(db):
    """旧调用不传 ledger_id：写入落在默认账本，读取不按账本过滤"""
    default_id = LedgerDAO.default_id()
    bill_id = BillDAO.create(
        {
            "tx_time": "2026-05-05 10:00:00",
            "account": "wechat",
            "tx_type": "expense",
            "merchant": "旧调用商户",
            "amount": 20.0,
            "category": "餐饮",
            "tx_id": "LEG-1",
        },
        USER_A,
    )
    assert BillDAO.get_by_id(bill_id, USER_A)["ledger_id"] == default_id
    assert default_id == DEFAULT_LEDGER_ID


def test_bills_are_scoped_by_ledger(db):
    default_id = LedgerDAO.default_id()
    other = LedgerDAO.create("装修账本")
    BillDAO.insert_many(make_bill_records(2, prefix="DEF"), USER_A)
    BillDAO.insert_many(make_bill_records(3, prefix="OTH"), USER_A, other["id"])

    total, rows = BillDAO.list_bills(USER_A, page_size=50)
    assert total == 5  # 不传 ledger_id = 不按账本过滤
    scoped, scoped_rows = BillDAO.list_bills(
        USER_A, page_size=50, ledger_id=other["id"]
    )
    assert scoped == 3 and all(r["ledger_id"] == other["id"] for r in scoped_rows)
    default_total, _ = BillDAO.list_bills(USER_A, page_size=50, ledger_id=default_id)
    assert default_total == 2


def test_budget_and_asset_are_scoped_by_ledger(db):
    other = LedgerDAO.create("装修账本")
    BudgetDAO.upsert(USER_A, "2026-09", "餐饮", 500.0)
    BudgetDAO.upsert(USER_A, "2026-09", "餐饮", 300.0, other["id"])

    assert len(BudgetDAO.list_month(USER_A, "2026-09")) == 2  # 不筛选 = 全部账本
    scoped = BudgetDAO.list_month(USER_A, "2026-09", other["id"])
    assert len(scoped) == 1 and scoped[0]["amount"] == 300.0

    AssetDAO.create(
        {
            "snap_date": "2026-09-01",
            "name": "装修专户",
            "asset_type": "asset",
            "amount": 5000,
        },
        USER_A,
        other["id"],
    )
    assert len(AssetDAO.list_snapshots(USER_A)) == 1
    assert len(AssetDAO.list_snapshots(USER_A, ledger_id=other["id"])) == 1


def test_delete_ledger_moves_data_to_default(db):
    """删除账本 ≠ 删除数据：流水/预算/快照并入默认账本"""
    default_id = LedgerDAO.default_id()
    other = LedgerDAO.create("装修账本")
    BillDAO.insert_many(make_bill_records(2, prefix="OTH"), USER_A, other["id"])
    BudgetDAO.upsert(USER_A, "2026-09", "餐饮", 300.0, other["id"])
    AssetDAO.create(
        {
            "snap_date": "2026-09-01",
            "name": "装修专户",
            "asset_type": "asset",
            "amount": 5000,
        },
        USER_A,
        other["id"],
    )

    moved = LedgerDAO.delete(other["id"])
    assert moved == {"bills": 2, "budgets": 1, "assets": 1, "dropped_budgets": 0}
    assert LedgerDAO.get(other["id"]) is None

    total, rows = BillDAO.list_bills(USER_A, page_size=50, ledger_id=default_id)
    assert total == 2 and all(r["ledger_id"] == default_id for r in rows)
    assert len(BudgetDAO.list_month(USER_A, "2026-09", default_id)) == 1
    assert len(AssetDAO.list_snapshots(USER_A, ledger_id=default_id)) == 1


def test_delete_default_ledger_refused(db):
    default_id = LedgerDAO.default_id()
    assert LedgerDAO.delete(default_id) is None
    with pytest.raises(ValidationError):
        ledger_service.delete_ledger(default_id)


def test_delete_ledger_service_returns_moved_counts(db):
    """服务层删除账本透出并入计数（评审项：docstring 承诺与返回值对齐）"""
    other = LedgerDAO.create("装修账本")
    BillDAO.insert_many(make_bill_records(3, prefix="MV"), USER_A, other["id"])
    BudgetDAO.upsert(USER_A, "2026-09", "餐饮", 300.0, other["id"])
    # 默认账本已有同键预算 → 来源侧预算被丢弃（dropped_budgets）
    BudgetDAO.upsert(USER_A, "2026-09", "餐饮", 100.0)

    result = ledger_service.delete_ledger(other["id"])
    assert result["moved_to_default"] is True
    assert result["moved_bills"] == 3
    assert result["moved_budgets"] == 0
    assert result["dropped_budgets"] == 1
    assert result["moved_assets"] == 0


def test_api_list_and_admin_guard(client, db):
    res = client.get("/api/ledgers", headers=A_HEADERS)
    assert res.status_code == 200
    assert res.json()["data"][0]["name"] == "默认账本"

    # 写操作限管理员：普通账号 403（路由守卫 require_admin）
    assert (
        client.post("/api/ledgers", json={"name": "x"}, headers=A_HEADERS).status_code
        == 403
    )
    created = client.post(
        "/api/ledgers", json={"name": "装修账本"}, headers=ADMIN_HEADERS
    )
    assert created.status_code == 201
    ledger_id = created.json()["data"]["id"]

    renamed = client.put(
        f"/api/ledgers/{ledger_id}",
        json={"name": "装修账本2"},
        headers=ADMIN_HEADERS,
    )
    assert renamed.status_code == 200 and renamed.json()["data"]["name"] == "装修账本2"

    deleted = client.delete(f"/api/ledgers/{ledger_id}", headers=ADMIN_HEADERS)
    assert (
        deleted.status_code == 200
        and deleted.json()["data"]["moved_to_default"] is True
    )

    # 删除默认账本被拒（400 业务校验）
    default_id = client.get("/api/ledgers", headers=A_HEADERS).json()["data"][0]["id"]
    assert (
        client.delete(f"/api/ledgers/{default_id}", headers=ADMIN_HEADERS).status_code
        == 400
    )
