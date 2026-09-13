"""备份与恢复测试：JSON 导出结构、合并/覆盖恢复、格式容错"""

import json

from app.db.dao.bill_dao import BillDAO
from app.db.dao.asset_dao import AssetDAO
from app.services import backup_service
from tests.conftest import USER_A, USER_B, make_bill_records

A_HEADERS = {"X-Trim-Userid": USER_A}
B_HEADERS = {"X-Trim-Userid": USER_B}
# 备份/恢复是全局影响操作，需要管理员身份（本地无网关头场景视为管理员）
ADMIN_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Isadmin": "true"}


def seed_all():
    BillDAO.insert_many(
        make_bill_records(2, prefix="BK", tags="出差", reimbursed=True, user_id=""),
        USER_A,
    )
    BillDAO.insert_many(make_bill_records(1, prefix="KB"), USER_B)
    AssetDAO.create(
        {
            "snap_date": "2026-09-01",
            "name": "存款",
            "asset_type": "asset",
            "amount": 1000,
        },
        USER_A,
    )


def test_backup_download_shape(client):
    seed_all()
    res = client.get("/api/settings/backup", headers=ADMIN_HEADERS)
    assert res.status_code == 200
    assert "application/json" in res.headers["content-type"]
    data = json.loads(res.content.decode("utf-8"))
    assert data["app"] == "fn-finstat"
    assert data["format_version"] == backup_service.BACKUP_FORMAT_VERSION
    assert len(data["bills"]) == 3
    assert data["bills"][0]["user_id"] in (USER_A, USER_B)
    assert "餐饮" in data["categories"]
    assert data["assets"][0]["name"] == "存款"
    assert data["budgets"] == []


def test_restore_merge_dedupes(client):
    seed_all()
    backup = backup_service.export_backup()

    # 合并恢复同一份备份 → 唯一键去重，账单无新增
    res = client.post(
        "/api/settings/restore",
        headers=ADMIN_HEADERS,
        data={"replace": "false"},
        files={
            "file": (
                "backup.json",
                json.dumps(backup).encode("utf-8"),
                "application/json",
            )
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["replaced"] is False
    assert body["bills"] == 3  # 提交 3 条（带 tx_id 全部去重）
    rows = client.get("/api/bill/list?page_size=50", headers=A_HEADERS).json()
    assert rows["total"] == 2  # 列表按账号隔离：A 只见自己的 2 条
    assert (
        client.get("/api/bill/list?page_size=50", headers=B_HEADERS).json()["total"]
        == 1
    )

    # 无交易号流水不去重，重复恢复会产生重复（文档化行为）
    backup["bills"] = [{**backup["bills"][0], "tx_id": None}]
    res = client.post(
        "/api/settings/restore",
        headers=ADMIN_HEADERS,
        files={
            "file": ("b.json", json.dumps(backup).encode("utf-8"), "application/json")
        },
    )
    assert res.status_code == 200
    rows = client.get("/api/bill/list?page_size=50", headers=A_HEADERS).json()
    assert rows["total"] == 3


def test_restore_replace_wipes_existing(client):
    seed_all()
    backup = backup_service.export_backup()
    # 只保留一条账单做覆盖恢复
    backup["bills"] = [backup["bills"][0]]

    res = client.post(
        "/api/settings/restore",
        headers=ADMIN_HEADERS,
        data={"replace": "true"},
        files={
            "file": ("b.json", json.dumps(backup).encode("utf-8"), "application/json")
        },
    )
    assert res.status_code == 200
    assert res.json()["replaced"] is True

    rows_a = client.get("/api/bill/list?page_size=50", headers=A_HEADERS).json()
    rows_b = client.get("/api/bill/list?page_size=50", headers=B_HEADERS).json()
    assert rows_a["total"] == 1 and rows_b["total"] == 0
    assets = client.get("/api/asset", headers=A_HEADERS).json()
    assert len(assets) == 1  # assets 节仍恢复
    assert client.get("/api/bill/recycle", headers=A_HEADERS).json()["total"] == 0


def test_restore_invalid_and_malformed_rows(client):
    res = client.post(
        "/api/settings/restore",
        headers=ADMIN_HEADERS,
        files={"file": ("b.json", b"not json", "application/json")},
    )
    assert res.status_code == 400

    res = client.post(
        "/api/settings/restore",
        headers=ADMIN_HEADERS,
        files={
            "file": (
                "b.json",
                json.dumps({"foo": 1}).encode("utf-8"),
                "application/json",
            )
        },
    )
    assert res.status_code == 400

    backup = {
        "bills": [
            {
                "tx_time": "2026-01-01 10:00:00",
                "tx_type": "expense",
                "amount": 10,
                "category": "新分类",
                "tx_id": "NEW-1",
            },
            {"tx_time": "", "tx_type": "expense", "amount": 5},  # 缺时间 → 跳过
            {
                "tx_time": "2026-01-02 10:00:00",
                "tx_type": "xxx",
                "amount": 5,
            },  # 非法类型 → 跳过
        ]
    }
    res = client.post(
        "/api/settings/restore",
        headers=ADMIN_HEADERS,
        files={
            "file": ("b.json", json.dumps(backup).encode("utf-8"), "application/json")
        },
    )
    body = res.json()
    assert body["bills"] == 1 and body["skipped"] == 2
    # 流水引用的「新分类」自动补建
    names = [c["name"] for c in client.get("/api/category", headers=A_HEADERS).json()]
    assert "新分类" in names


def test_backup_requires_admin(client):
    """多账号模式下备份/恢复仅限管理员（本地无网关头不受影响）"""
    seed_all()
    res = client.get("/api/settings/backup", headers=A_HEADERS)
    assert res.status_code == 403
    res = client.post(
        "/api/settings/restore",
        files={"file": ("b.json", b"{}", "application/json")},
        headers=A_HEADERS,
    )
    assert res.status_code == 403
    # 本地模式（无任何网关头）视为唯一用户，不受限
    assert client.get("/api/settings/backup").status_code == 200
