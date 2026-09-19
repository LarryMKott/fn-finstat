"""储蓄目标测试（T-1.4）：结余自动计入进度、达成判定、隐私与校验

行为底线：
- 进度 = 目标起始日以来（收入 − 支出）的累计净结余，由流水实时计算
- 净结余达到目标金额自动 done；目标日给出「所需月均结余」参考
- 数据按账号隔离
"""

from app.db.dao.bill_dao import BillDAO
from tests.conftest import USER_A, USER_B, make_bill_records

A_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Username": "zhangsan"}
B_HEADERS = {"X-Trim-Userid": USER_B, "X-Trim-Username": "lisi"}


def _seed(tx_id, tx_time, tx_type, amount):
    BillDAO.insert_many(
        make_bill_records(
            1,
            prefix=tx_id,
            tx_time=tx_time,
            tx_type=tx_type,
            amount=amount,
            category="其他",
        ),
        USER_A,
    )


def test_goal_progress_from_bills(client, db):
    """结余自动计入：起始日以来的收入 − 支出实时推进进度"""
    created = client.post(
        "/api/savings-goals",
        json={"name": "应急金", "target_amount": 1000, "target_date": "2026-12-31"},
        headers=A_HEADERS,
    )
    assert created.status_code == 201
    goal = created.json()["data"]
    assert goal["saved"] == 0 and goal["done"] is False
    assert goal["months_left"] is not None

    # 起始日之后：收入 1200 − 支出 300 = 结余 900（未达标）
    _seed("SG-I", "2026-09-25 10:00:00", "income", 1200)
    _seed("SG-E", "2026-09-26 10:00:00", "expense", 300)
    listed = client.get("/api/savings-goals", headers=A_HEADERS).json()["data"]["items"]
    goal = [g for g in listed if g["name"] == "应急金"][0]
    assert goal["saved"] == 900
    assert goal["remaining"] == 100
    assert goal["pct"] == 90
    assert goal["done"] is False
    assert goal["per_month_needed"] >= 0

    # 再攒 100 → 达标自动 done
    _seed("SG-I2", "2026-09-27 10:00:00", "income", 100)
    listed = client.get("/api/savings-goals", headers=A_HEADERS).json()["data"]["items"]
    goal = [g for g in listed if g["name"] == "应急金"][0]
    assert goal["saved"] == 1000
    assert goal["done"] is True and goal["pct"] == 100

    # 起始日之前的流水不计入
    _seed("SG-OLD", "2026-08-01 10:00:00", "expense", 5000)
    listed = client.get("/api/savings-goals", headers=A_HEADERS).json()["data"]["items"]
    goal = [g for g in listed if g["name"] == "应急金"][0]
    assert goal["saved"] == 1000


def test_goal_privacy_and_validation(client, db):
    created = client.post(
        "/api/savings-goals",
        json={"name": "私房钱", "target_amount": 500},
        headers=A_HEADERS,
    )
    goal_id = created.json()["data"]["id"]

    # B 看不到 A 的目标，也改不了 / 删不了
    assert (
        client.get("/api/savings-goals", headers=B_HEADERS).json()["data"]["items"]
        == []
    )
    assert (
        client.put(
            f"/api/savings-goals/{goal_id}",
            json={"name": "偷改"},
            headers=B_HEADERS,
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/savings-goals/{goal_id}", headers=B_HEADERS).status_code
        == 404
    )

    # 校验：空名称 / 非正金额
    assert client.post(
        "/api/savings-goals", json={"name": "", "target_amount": 100}, headers=A_HEADERS
    ).status_code in (400, 422)
    assert (
        client.post(
            "/api/savings-goals",
            json={"name": "X", "target_amount": 0},
            headers=A_HEADERS,
        ).status_code
        == 422
    )

    # 删除后 404
    assert (
        client.delete(f"/api/savings-goals/{goal_id}", headers=A_HEADERS).status_code
        == 204
    )
    assert (
        client.put(
            f"/api/savings-goals/{goal_id}", json={"name": "X"}, headers=A_HEADERS
        ).status_code
        == 404
    )


def test_goal_update_recomputes_progress(client, db):
    _seed("SG-U", "2026-09-25 10:00:00", "income", 200)
    created = client.post(
        "/api/savings-goals",
        json={"name": "旅行基金", "target_amount": 800},
        headers=A_HEADERS,
    )
    goal_id = created.json()["data"]["id"]
    assert created.json()["data"]["pct"] == 25

    # 目标金额调低到已达到的水平 → 自动 done
    updated = client.put(
        f"/api/savings-goals/{goal_id}", json={"target_amount": 150}, headers=A_HEADERS
    ).json()["data"]
    assert updated["done"] is True
    assert updated["pct"] == 100
