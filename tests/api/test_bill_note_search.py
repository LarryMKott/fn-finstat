"""备注语义检索端点测试（AI-8）：GET /api/bill/note-search

覆盖：响应结构（query/indexed/results/caliber）、网关身份隔离、
参数校验（缺 q → 422 统一响应体、空 q → 服务层 400）。
"""

from app.db.dao.bill_dao import BillDAO
from tests.conftest import USER_A, USER_B, make_bill_records

A_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Username": "zhangsan"}
B_HEADERS = {"X-Trim-Userid": USER_B}


def _seed(user: str, prefix: str) -> None:
    BillDAO.insert_many(
        make_bill_records(
            2,
            prefix=prefix,
            merchant="天猫超市",
            remark="给老妈买的按摩仪",
        ),
        user,
    )


def test_note_search_missing_q_is_unprocessable(client, db):
    """缺检索词 → 422 统一响应体（FastAPI 参数校验 + 全局异常包装）"""
    res = client.get("/api/bill/note-search", headers=A_HEADERS)
    assert res.status_code == 422
    body = res.json()
    assert body["code"] != 0 and body["data"] is None


def test_note_search_returns_ranked_hits(client, db):
    """命中结果带 score 与完整流水行；caliber 随响应回传"""
    _seed(USER_A, "NS-API-A")
    res = client.get(
        "/api/bill/note-search", params={"q": "给家里人买东西"}, headers=A_HEADERS
    )
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["query"] == "给家里人买东西"
    assert data["indexed"] == 2
    assert len(data["results"]) == 2
    first = data["results"][0]
    assert {"id", "tx_time", "merchant", "remark", "amount", "score"} <= set(first)
    assert first["score"] > 0
    assert data["caliber"]["fields"] == "商户+备注"


def test_note_search_user_isolation(client, db):
    """B 账号检索不到 A 的流水，反之亦然"""
    _seed(USER_A, "NS-API-A")
    res = client.get("/api/bill/note-search", params={"q": "按摩仪"}, headers=B_HEADERS)
    assert res.status_code == 200
    assert res.json()["data"]["results"] == []

    _seed(USER_B, "NS-API-B")
    res = client.get("/api/bill/note-search", params={"q": "按摩仪"}, headers=A_HEADERS)
    remarks = {r["remark"] for r in res.json()["data"]["results"]}
    assert remarks == {"给老妈买的按摩仪"}


def test_note_search_blank_q_is_bad_request(client, db):
    """纯空白检索词：服务层 BizError → 400 + BILL_INVALID 错误码"""
    res = client.get("/api/bill/note-search", params={"q": "   "}, headers=A_HEADERS)
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == 40001
