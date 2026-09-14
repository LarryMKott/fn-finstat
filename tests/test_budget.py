"""月度预算功能测试：CRUD、进度计算与账号隔离"""

import pytest
from app.core.errors import ValidationError

from app.db.dao.bill_dao import BillDAO
from tests.conftest import USER_A, USER_B, make_bill_records

A_HEADERS = {"X-Trim-Userid": USER_A}
B_HEADERS = {"X-Trim-Userid": USER_B}


def test_upsert_and_overview(client):
    # 分类预算 + 总预算
    res = client.put(
        "/api/budget",
        headers=A_HEADERS,
        json={"month": "2026-09", "category": "餐饮", "amount": 1000},
    )
    assert res.status_code == 200
    res = client.put(
        "/api/budget",
        headers=A_HEADERS,
        json={"month": "2026-09", "category": "", "amount": 3000},
    )
    assert res.status_code == 200

    BillDAO.insert_many(
        make_bill_records(
            2, prefix="BG", tx_time="2026-09-10 12:00:00", category="餐饮", amount=300
        ),
        USER_A,
    )
    data = client.get("/api/budget?month=2026-09", headers=A_HEADERS).json()["data"]
    assert data["month"] == "2026-09"
    assert data["total_budget"] == 3000  # 有总预算行时以总预算为准
    assert data["total_expense"] == 600
    items = {i["category"]: i for i in data["items"]}
    assert items[""]["budget"] == 3000 and items[""]["expense"] == 600
    assert items["餐饮"]["budget"] == 1000
    assert items["餐饮"]["expense"] == 600  # 2 条 × 300
    assert items["餐饮"]["remaining"] == 400


def test_overview_without_total_budget_sums_categories(client):
    client.put(
        "/api/budget",
        headers=A_HEADERS,
        json={"month": "2026-08", "category": "交通", "amount": 200},
    )
    client.put(
        "/api/budget",
        headers=A_HEADERS,
        json={"month": "2026-08", "category": "餐饮", "amount": 800},
    )
    data = client.get("/api/budget?month=2026-08", headers=A_HEADERS).json()["data"]
    assert data["total_budget"] == 1000
    assert data["total_expense"] == 0  # 无流水
    assert all(i["expense"] == 0 for i in data["items"])


def test_upsert_updates_existing(client):
    client.put(
        "/api/budget",
        headers=A_HEADERS,
        json={"month": "2026-07", "category": "购物", "amount": 100},
    )
    client.put(
        "/api/budget",
        headers=A_HEADERS,
        json={"month": "2026-07", "category": "购物", "amount": 500},
    )
    data = client.get("/api/budget?month=2026-07", headers=A_HEADERS).json()["data"]
    assert len(data["items"]) == 1
    assert data["items"][0]["budget"] == 500


def test_delete_budget(client):
    client.put(
        "/api/budget",
        headers=A_HEADERS,
        json={"month": "2026-06", "category": "娱乐", "amount": 100},
    )
    budget_id = client.get("/api/budget?month=2026-06", headers=A_HEADERS).json()[
        "data"
    ]["items"][0]["id"]
    assert (
        client.delete(f"/api/budget/{budget_id}", headers=A_HEADERS).status_code == 204
    )
    assert (
        client.get("/api/budget?month=2026-06", headers=A_HEADERS).json()["data"][
            "items"
        ]
        == []
    )
    assert (
        client.delete(f"/api/budget/{budget_id}", headers=A_HEADERS).status_code == 404
    )


def test_budget_scoped_by_user(client):
    client.put(
        "/api/budget",
        headers=A_HEADERS,
        json={"month": "2026-05", "category": "餐饮", "amount": 100},
    )
    data = client.get("/api/budget?month=2026-05", headers=B_HEADERS).json()["data"]
    assert data["items"] == []  # B 看不到 A 的预算
    assert data["total_budget"] == 0


def test_budget_validation(client):
    # 月份格式
    assert client.get("/api/budget?month=2026-13", headers=A_HEADERS).status_code == 400
    assert client.get("/api/budget?month=bad", headers=A_HEADERS).status_code == 400
    # 分类不存在
    res = client.put(
        "/api/budget",
        headers=A_HEADERS,
        json={"month": "2026-09", "category": "不存在", "amount": 10},
    )
    assert res.status_code == 400
    # 金额非法
    res = client.put(
        "/api/budget",
        headers=A_HEADERS,
        json={"month": "2026-09", "category": "餐饮", "amount": 0},
    )
    assert res.status_code == 422


def test_service_month_range():
    from app.utils.period import (
        month_range,
    )  # 从公共周期工具导入（原 bill_service.month_range）

    assert month_range("2026-09") == ("2026-09-01", "2026-09-30")
    assert month_range("2024-02") == ("2024-02-01", "2024-02-29")
    with pytest.raises(ValidationError):
        month_range("2026-13")
