"""权限中间件 × 开放 API Token 的边界（T-1.2 回归）

背景：中间件为「无网关头但带 Token」的请求开了专门分支（Token 恒只读、
到不了管理面）。该分支必须**仅在请求确实没有网关身份时**生效 —— 否则
本中间件本该执行的身份与管理面判定会被整体跳过。

历史缺陷（本次修复）：判定条件只有 ``has_api_token(raw)``，只要请求头里
出现 ``Authorization: Bearer <任意值>`` 或 ``X-Api-Token``，无论是否携带
合法网关身份、无论 Token 是否有效，都会走进该分支。后果是带网关身份的
普通写请求被误判为「Token 只读请求」并回 403（文案误导：用户并未使用
Token），即浏览器侧任何给请求附加 Authorization 头的路径都会让写操作
全线失效。

本文件用网关模式（TRIM_PKGVAR 存在）下的最简 app 覆盖判定，不依赖数据库：
管理面路径命中即 401/403，未命中则放行到路由层（404），因此足以区分
「中间件拦了」与「中间件放行了」。
"""

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def gateway_env(tmp_path, monkeypatch):
    """以网关模式（fnOS）重载配置与中间件模块，产出可观察判定的最简 app

    网关模式由 TRIM_PKGVAR 存在与否决定，因此在测试内重载模块而非改动
    全局环境；退出时统一 undo 并再次重载，避免污染同进程内的其他测试。
    """
    monkeypatch.setenv("TRIM_PKGVAR", str(tmp_path))
    monkeypatch.delenv("FNOS_ALLOW_HEADERLESS", raising=False)

    import app.config as config
    import app.core.context as context
    import app.core.middleware as middleware
    import app.core.permissions as permissions

    modules = (config, context, permissions, middleware)

    def reload_all():
        for mod in modules:
            importlib.reload(mod)

    reload_all()
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
        "Authorization": "Bearer ffk_" + "0" * 32,
    }
    res = client.post("/api/bill", headers=headers, json={})

    # 路由未注册 -> 404；关键是**不能**是「Token 仅支持只读访问」的 403
    assert res.status_code == 404, res.text
    assert "只读" not in res.text


def test_headerless_token_request_still_hits_token_branch(gateway_env):
    """无网关身份 + 带 Token：仍走 Token 分支（只读放行 / 写方法与管理面拒绝）"""
    client, config = gateway_env
    assert config.IS_FNOS is True

    token_headers = {"Authorization": "Bearer ffk_" + "0" * 32}

    # 非管理面写方法 -> 403 只读
    write = client.post("/api/bill", headers=token_headers, json={})
    assert write.status_code == 403
    assert "只读" in write.json()["msg"]

    # 无 Token 且无网关身份 -> 401（身份未知，与 Token 分支区分开）
    anonymous = client.post("/api/bill", headers={}, json={})
    assert anonymous.status_code == 401
