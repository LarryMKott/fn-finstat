"""权限中间件 × 开放 API Token 的边界（T-1.2 回归）

背景：中间件为「无网关头但带 Token」的请求开了专门分支（Token 恒只读、
到不了管理面）。该分支必须**仅在请求确实没有网关身份时**生效 —— 否则
本中间件本该执行的身份与管理面判定会被整体跳过。

历史缺陷一（已修复）：判定条件只有 ``has_api_token(raw)``，只要请求头里
出现 ``Authorization: Bearer <任意值>`` 或 ``X-Api-Token``，都会走进该分支。
后果是带网关身份的普通写请求被误判为「Token 只读请求」并回 403。

历史缺陷二（本次修复）：分支内只检查「带了 Token 串」就放行只读请求，
验证推给路由层 deps——无身份依赖的路由（GET /api/category、/docs 等）
不验证，垃圾字符串即可绕过 401。现在中间件内即验证：无效 Token 一律
401 fail-closed；仅认 ffk_ 前缀凭证（前置代理注入的其他 Bearer 不误判）；
验证通过的身份暂存 scope 供 deps.get_identity 复用。

本文件用网关模式（TRIM_PKGVAR 存在）下的最简 app 覆盖判定，authenticate
以 monkeypatch 替身模拟（不依赖真实数据库），足以区分「中间件拦了」与
「中间件放行了」。
"""

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

VALID_TOKEN = "ffk_" + "0" * 32


@pytest.fixture()
def gateway_env(tmp_path, monkeypatch):
    """以网关模式（fnOS）重载配置与中间件模块，产出可观察判定的最简 app

    网关模式由 TRIM_PKGVAR 存在与否决定，因此在测试内重载模块而非改动
    全局环境；退出时统一 undo 并再次重载，避免污染同进程内的其他测试。
    authenticate 用替身：仅 VALID_TOKEN 有效（绑定 token-user 账号），
    其余（含 ffk_ 形状但未签发、非 ffk_ 前缀）一律无效。
    """
    monkeypatch.setenv("TRIM_PKGVAR", str(tmp_path))
    monkeypatch.delenv("FNOS_ALLOW_HEADERLESS", raising=False)

    import app.config as config
    import app.core.context as context
    import app.core.middleware as middleware
    import app.core.permissions as permissions
    import app.services.token_service as token_service

    modules = (config, context, permissions, middleware)

    def reload_all():
        for mod in modules:
            importlib.reload(mod)

    reload_all()

    def fake_authenticate(raw_token: str, client_key: str = ""):
        from app.core.context import GatewayUser

        if raw_token == VALID_TOKEN:
            return GatewayUser(user_id="token-user", user_name="", is_admin=False)
        return None

    monkeypatch.setattr(token_service, "authenticate", fake_authenticate)

    app = FastAPI()
    middleware.add_app_middlewares(app)
    try:
        yield TestClient(app), config
    finally:
        monkeypatch.undo()
        reload_all()


def test_write_with_gateway_identity_is_not_treated_as_token_request(gateway_env):
    """带网关身份的写请求不得被 Token 分支误判为「Token 只读请求」（回归）"""
    client, config = gateway_env
    assert config.IS_FNOS is True, "本用例要求网关模式"

    headers = {
        "X-Trim-Userid": "u1",
        "X-Trim-Username": "u1",
        # 网关头优先：Token 不参与鉴权，其内容与有效性都不应影响判定
        "Authorization": "Bearer " + VALID_TOKEN,
    }
    res = client.post("/api/bill", headers=headers, json={})

    # 路由未注册 -> 404；关键是**不能**是「Token 仅支持只读访问」的 403
    assert res.status_code == 404, res.text
    assert "只读" not in res.text


def test_valid_token_read_passes_write_rejected(gateway_env):
    """有效 Token：只读放行进路由，写方法与管理面在中间件 403"""
    client, _ = gateway_env
    token_headers = {"Authorization": "Bearer " + VALID_TOKEN}

    write = client.post("/api/bill", headers=token_headers, json={})
    assert write.status_code == 403
    assert "只读" in write.json()["msg"]

    read = client.get("/api/bill/list", headers=token_headers)
    assert read.status_code == 404  # 放行进路由（路由未注册），而非 401/403


def test_invalid_token_is_rejected_before_surface_checks(gateway_env):
    """无效 Token（含 ffk_ 形状但未签发）：一律 401，不再放行只读请求（回归）"""
    client, _ = gateway_env
    bogus = {"Authorization": "Bearer " + "ffk_" + "f" * 32}

    read = client.get("/api/bill/list", headers=bogus)
    assert read.status_code == 401
    assert "无效" in read.json()["msg"]

    write = client.post("/api/bill", headers=bogus, json={})
    assert write.status_code == 401


def test_non_prefixed_bearer_is_not_our_token(gateway_env):
    """非 ffk_ 前缀的 Bearer（如前置代理注入的凭证）不按本应用 Token 处理：
    走匿名分支——网关模式下无身份头仍 401，但不回「无效 Token」文案"""
    client, _ = gateway_env
    foreign = {"Authorization": "Bearer some-proxy-credential"}

    res = client.get("/api/bill/list", headers=foreign)
    assert res.status_code == 401
    assert "无效" not in res.json()["msg"]


def test_anonymous_without_token_still_401(gateway_env):
    """无 Token 且无网关身份 -> 401（身份未知，与 Token 分支区分开）"""
    client, _ = gateway_env
    anonymous = client.post("/api/bill", headers={}, json={})
    assert anonymous.status_code == 401
