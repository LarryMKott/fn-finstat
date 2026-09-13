"""资产快照（净资产追踪）测试：CRUD、趋势汇总与账号隔离"""

from app.db.dao.asset_dao import AssetDAO
from tests.conftest import USER_A, USER_B

A_HEADERS = {"X-Trim-Userid": USER_A}
B_HEADERS = {"X-Trim-Userid": USER_B}


def test_create_and_list(client):
    res = client.post(
        "/api/asset",
        headers=A_HEADERS,
        json={
            "snap_date": "2026-09-01",
            "name": "招商储蓄卡",
            "asset_type": "asset",
            "amount": 50000,
        },
    )
    assert res.status_code == 201
    body = res.json()["data"]
    assert body["name"] == "招商储蓄卡" and body["amount"] == 50000
    res = client.post(
        "/api/asset",
        headers=A_HEADERS,
        json={
            "snap_date": "2026-09-01",
            "name": "房贷",
            "asset_type": "liability",
            "amount": 300000,
            "remark": "月供",
        },
    )
    assert res.status_code == 201
    rows = client.get("/api/asset", headers=A_HEADERS).json()["data"]
    assert len(rows) == 2
    assert rows[0]["snap_date"] >= rows[1]["snap_date"]  # 日期倒序


def test_update_and_delete(client):
    asset_id = client.post(
        "/api/asset",
        headers=A_HEADERS,
        json={
            "snap_date": "2026-08-01",
            "name": "余额宝",
            "asset_type": "asset",
            "amount": 1000,
        },
    ).json()["data"]["id"]
    res = client.put(
        f"/api/asset/{asset_id}",
        headers=A_HEADERS,
        json={"amount": 1200.5, "remark": "更新"},
    )
    assert res.status_code == 200
    assert res.json()["data"]["amount"] == 1200.5
    assert res.json()["data"]["remark"] == "更新"
    assert client.delete(f"/api/asset/{asset_id}", headers=A_HEADERS).status_code == 204
    assert client.get("/api/asset", headers=A_HEADERS).json()["data"] == []
    # 再删一次 → 404
    assert client.delete(f"/api/asset/{asset_id}", headers=A_HEADERS).status_code == 404


def test_trend_groups_by_date(client):
    for date, name, atype, amount in [
        ("2026-01-01", "存款", "asset", 1000),
        ("2026-01-01", "花呗", "liability", 200),
        ("2026-02-01", "存款", "asset", 1500),
    ]:
        client.post(
            "/api/asset",
            headers=A_HEADERS,
            json={
                "snap_date": date,
                "name": name,
                "asset_type": atype,
                "amount": amount,
            },
        )
    trend = client.get("/api/asset/trend", headers=A_HEADERS).json()["data"]
    assert len(trend) == 2
    assert trend[0] == {
        "date": "2026-01-01",
        "assets": 1000,
        "liabilities": 200,
        "net": 800,
    }
    assert trend[1] == {
        "date": "2026-02-01",
        "assets": 1500,
        "liabilities": 0,
        "net": 1500,
    }


def test_scoped_by_user(client):
    AssetDAO.create(
        {
            "snap_date": "2026-07-01",
            "name": "A 的资产",
            "asset_type": "asset",
            "amount": 10,
        },
        USER_A,
    )
    assert len(client.get("/api/asset", headers=A_HEADERS).json()["data"]) == 1
    assert client.get("/api/asset", headers=B_HEADERS).json()["data"] == []
    assert client.get("/api/asset/trend", headers=B_HEADERS).json()["data"] == []
    # B 改/删 A 的快照 → 404
    a_id = client.get("/api/asset", headers=A_HEADERS).json()["data"][0]["id"]
    assert (
        client.put(
            f"/api/asset/{a_id}", headers=B_HEADERS, json={"amount": 1}
        ).status_code
        == 404
    )
    assert client.delete(f"/api/asset/{a_id}", headers=B_HEADERS).status_code == 404


def test_validation(client):
    res = client.post(
        "/api/asset",
        headers=A_HEADERS,
        json={
            "snap_date": "2026-13-40",
            "name": "x",
            "asset_type": "asset",
            "amount": 1,
        },
    )
    assert res.status_code == 400  # 非法日期
    res = client.post(
        "/api/asset",
        headers=A_HEADERS,
        json={
            "snap_date": "2026-09-01",
            "name": "x",
            "asset_type": "crypto",
            "amount": 1,
        },
    )
    assert res.status_code == 422  # asset_type 字面量约束
    res = client.post(
        "/api/asset",
        headers=A_HEADERS,
        json={
            "snap_date": "2026-09-01",
            "name": "",
            "asset_type": "asset",
            "amount": 1,
        },
    )
    assert res.status_code == 422  # 名称为空
