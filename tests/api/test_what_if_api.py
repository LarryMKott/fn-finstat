"""What-if 反事实模拟端点测试（AI-9）：GET /api/forecast/what-if 基线 +
POST 情景计算。覆盖：响应结构、网关身份隔离、参数校验（未知分类 400 /
空清单与越界月数 422）。"""

from app.db.dao.bill_dao import BillDAO
from tests.conftest import USER_A, USER_B, make_bill_records

A_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Username": "zhangsan"}
B_HEADERS = {"X-Trim-Userid": USER_B}


def _seed(user: str, prefix: str) -> None:
    for m in ("2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"):
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"{prefix}-{m}",
                tx_time=f"{m}-05 10:00:00",
                tx_type="expense",
                amount=1500.0,
                merchant="外卖平台",
                category="餐饮",
            ),
            user,
        )


def test_what_if_baseline_shape_and_isolation(client, db):
    """基线返回分类月均与月结余；账号之间互不可见"""
    _seed(USER_A, "WIF-A")
    res = client.get("/api/forecast/what-if", headers=A_HEADERS)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["window"]["months"] == 6
    assert data["baseline"]["categories"] == [
        {"category": "餐饮", "monthly_amount": 1500.0}
    ]
    assert data["scenario"] is None
    assert data["caliber"]["linear"]

    empty = client.get("/api/forecast/what-if", headers=B_HEADERS)
    assert empty.json()["data"]["baseline"]["categories"] == []


def test_what_if_scenario_computes_delta(client, db):
    """POST 情景：差额、累计与调整后月结余按口径返回"""
    _seed(USER_A, "WIF-A")
    payload = {
        "adjustments": [{"category": "餐饮", "monthly_amount": 800}],
        "months": 12,
    }
    res = client.post("/api/forecast/what-if", json=payload, headers=A_HEADERS)
    assert res.status_code == 200
    data = res.json()["data"]
    s = data["scenario"]
    assert s["delta_monthly"] == 700.0
    assert s["cumulative_delta"] == 8400.0
    assert s["monthly_savings_after"] == -800.0  # 基线结余 −1500（只有支出）+ 700


def test_what_if_unknown_category_is_bad_request(client, db):
    """未知分类：服务层 ValidationError → 400 + 统一错误码"""
    _seed(USER_A, "WIF-A")
    payload = {"adjustments": [{"category": "不存在", "monthly_amount": 100}]}
    res = client.post("/api/forecast/what-if", json=payload, headers=A_HEADERS)
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == 10001 and "不存在" in body["msg"]


def test_what_if_request_validation(client, db):
    """空调整清单 / 越界月数：pydantic 校验 422 统一响应体"""
    for payload in (
        {"adjustments": []},
        {"adjustments": [{"category": "餐饮", "monthly_amount": 10}], "months": 99},
    ):
        res = client.post("/api/forecast/what-if", json=payload, headers=A_HEADERS)
        assert res.status_code == 422
        body = res.json()
        assert body["code"] != 0 and body["data"] is None
