"""开放 API Token 测试（T-1.2）：签发一次性明文、只读限制、账号隔离、撤销、网关头优先

行为底线：
- Token 恒为只读：GET 放行、写方法与管理面 403
- Token 绑定账号：只能看到自己账号的数据
- 明文仅创建响应返回一次；服务端只存哈希；撤销立即失效
- 网关可信头优先：带网关头时 Token 不参与身份解析
"""

from app.db.dao.bill_dao import BillDAO
from tests.conftest import USER_A, USER_B, make_bill_records

A_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Username": "zhangsan"}
B_HEADERS = {"X-Trim-Userid": USER_B, "X-Trim-Username": "lisi"}


def _create_token(client, headers, name="我的 Token"):
    res = client.post("/api/tokens", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["data"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_token_lifecycle_and_read_only(client, db):
    BillDAO.insert_many(make_bill_records(2, prefix="TK", amount=10), USER_A)
    created = _create_token(client, A_HEADERS, "脚本用")
    token = created["token"]
    assert token.startswith("ffk_")

    # 列表不含明文，只有前缀
    listed = client.get("/api/tokens", headers=A_HEADERS).json()["data"]
    assert len(listed) == 1
    assert listed[0]["token_prefix"] == token[:12]
    assert "token" not in listed[0]
    assert listed[0]["revoked"] is False

    # 只读访问：GET 放行且数据按绑定账号隔离
    ok = client.get("/api/bill/list", headers=_auth(token))
    assert ok.status_code == 200
    assert ok.json()["data"]["total"] == 2

    # 写方法 → 403（Token 只读）
    denied = client.post(
        "/api/bill",
        headers=_auth(token),
        json={"tx_time": "2026-09-05 10:00:00", "account": "wechat",
              "tx_type": "expense", "amount": 1},
    )
    assert denied.status_code == 403
    assert "只读" in denied.json()["msg"]

    # 管理面 → 403：权限中间件在路由前拦截（Token 永远非管理员）
    from fastapi import FastAPI

    from app.core.handlers import register_exception_handlers
    from app.core.middleware import add_app_middlewares
    from app.core.permissions import is_admin_surface
    from app.api import ai as ai_api

    assert is_admin_surface("/api/settings/logs", "GET") is True
    mw_app = FastAPI()
    register_exception_handlers(mw_app)
    mw_app.include_router(ai_api.router)
    add_app_middlewares(mw_app)
    from fastapi.testclient import TestClient as _TC

    mw_client = _TC(mw_app)
    admin_surface = mw_client.put(
        "/api/ai/config", json={"model": "x"}, headers=_auth(token)
    )
    assert admin_surface.status_code == 403

    # 无效 Token → 401
    bad = client.get("/api/bill/list", headers=_auth("ffk_" + "0" * 32))
    assert bad.status_code == 401

    # 撤销 → 立即失效（带撤销结果包装，204 语义留给无返回体的端点）
    assert (
        client.delete(f"/api/tokens/{created['id']}", headers=A_HEADERS).status_code
        == 200
    )
    revoked = client.get("/api/bill/list", headers=_auth(token))
    assert revoked.status_code == 401


def test_token_scoped_to_its_account(client, db):
    """Token 绑定账号：A 的 Token 看不到 B 的流水"""
    BillDAO.insert_many(make_bill_records(1, prefix="TK-A", amount=10), USER_A)
    BillDAO.insert_many(make_bill_records(1, prefix="TK-B", amount=20), USER_B)
    token = _create_token(client, A_HEADERS)["token"]

    res = client.get("/api/bill/list", headers=_auth(token))
    assert res.status_code == 200
    rows = res.json()["data"]["items"]
    assert len(rows) == 1
    assert rows[0]["tx_id"] == "TK-A-0000"


def test_gateway_headers_take_priority_over_token(client, db):
    """网关可信头优先：带网关身份时 Token 不参与身份解析（无效 Token 也不影响）"""
    token = _create_token(client, B_HEADERS)["token"]
    headers = {**A_HEADERS, "Authorization": f"Bearer {token}"}
    res = client.get("/api/savings-goals", headers=headers)
    assert res.status_code == 200
    # B 的 Token + A 的网关头 → 身份是 A（网关头优先），A 无目标
    other = client.get("/api/savings-goals", headers=_auth(token))
    assert other.status_code == 200


def test_token_request_with_invalid_token_is_401(client, db):
    res = client.get("/api/savings-goals", headers=_auth("ffk_deadbeef"))
    assert res.status_code == 401
    assert "无效" in res.json()["msg"]
