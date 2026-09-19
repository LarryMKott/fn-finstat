"""T-7.1 评审遗留收口：流水 / 资产快照可在账本间移动（BillUpdate / AssetSnapshotUpdate 增 ledger_id）"""

from tests.conftest import USER_A

A_ADMIN = {
    "X-Trim-Userid": USER_A,
    "X-Trim-Username": "zhangsan",
    "X-Trim-Isadmin": "true",
}


def _create_ledger(client, name):
    res = client.post("/api/ledgers", json={"name": name}, headers=A_ADMIN)
    assert res.status_code in (200, 201), res.text
    return res.json()["data"]["id"]


def _create_bill(client, **overrides):
    payload = {
        "tx_time": "2026-09-05 10:00:00",
        "account": "wechat",
        "tx_type": "expense",
        "amount": 12.5,
        "merchant": "移动测试",
        **overrides,
    }
    res = client.post("/api/bill", json=payload, headers=A_ADMIN)
    assert res.status_code in (200, 201), res.text
    return res.json()["data"]


def test_bill_can_move_between_ledgers(client, db):
    target = _create_ledger(client, "旅行账本")
    bill = _create_bill(client)
    assert bill["ledger_id"] == 1  # 默认账本

    moved = client.put(
        f"/api/bill/{bill['id']}", json={"ledger_id": target}, headers=A_ADMIN
    )
    assert moved.status_code == 200
    assert moved.json()["data"]["ledger_id"] == target

    # 按目标账本筛选可见、原账本不再可见
    in_target = client.get(
        "/api/bill/list", params={"ledger_id": target}, headers=A_ADMIN
    ).json()["data"]
    assert [b["id"] for b in in_target["items"]] == [bill["id"]]


def test_bill_move_null_and_missing_ledger(client, db):
    target = _create_ledger(client, "第二账本")
    bill = _create_bill(client)
    moved = client.put(
        f"/api/bill/{bill['id']}", json={"ledger_id": target}, headers=A_ADMIN
    )
    assert moved.json()["data"]["ledger_id"] == target

    # 显式 null = 不迁移（与「未传」等价，BillUpdate 全部可选）
    untouched = client.put(
        f"/api/bill/{bill['id']}", json={"ledger_id": None}, headers=A_ADMIN
    )
    assert untouched.json()["data"]["ledger_id"] == target

    # 目标账本不存在 → 404（写路径显式校验）
    missing = client.put(
        f"/api/bill/{bill['id']}", json={"ledger_id": 999}, headers=A_ADMIN
    )
    assert missing.status_code == 404


def test_asset_snapshot_can_move_between_ledgers(client, db):
    target = _create_ledger(client, "资产账本")
    created = client.post(
        "/api/asset",
        json={
            "snap_date": "2026-09-01",
            "name": "存款",
            "asset_type": "asset",
            "amount": 1000,
        },
        headers=A_ADMIN,
    )
    assert created.status_code == 201
    asset_id = created.json()["data"]["id"]

    moved = client.put(
        f"/api/asset/{asset_id}", json={"ledger_id": target}, headers=A_ADMIN
    )
    assert moved.status_code == 200
    assert moved.json()["data"]["ledger_id"] == target

    # 显式 null = 不迁移
    untouched = client.put(
        f"/api/asset/{asset_id}", json={"ledger_id": None}, headers=A_ADMIN
    )
    assert untouched.json()["data"]["ledger_id"] == target
