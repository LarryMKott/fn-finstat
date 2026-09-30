"""Token 权限面系统性覆盖（T-1.2 收口验证）

为什么单独建这个文件：tests/conftest.py 的 client **不挂 add_app_middlewares**
（只注册异常处理器），权限中间件的行为在既有测试里覆盖极薄 ——
test_tokens.py 虽自建了 app，但只挂了 ai 一个 router。

本文件用「网关模式 + 完整中间件栈 + 全部业务路由」建立真实环境，对
**每一个** API 路径断言 Token 的准入结论，覆盖三层：
  1. 只读面：无网关头 + GET + 非管理面 → 放行，不得 5xx / 不得 401
  2. 写方法：任何写方法（POST/PUT/DELETE/PATCH）→ 被拦
  3. 管理面：命中 _ADMIN_RULES 的 GET → 被拦

路径清单取自真实 app 的 OpenAPI 文档，因此**新增路由自动纳入覆盖**，
策略表漏配（新管理面端点忘加规则）会在下一轮测试即暴露。

三个用例均经「缺陷化验证」确认为真回归（2026-09-19）：
  - 只读面被误拒        → test_token_read_surface_stays_healthy 失败
  - 写/管理面拦截移除    → test_token_cannot_reach_any_restricted_endpoint 失败
  - 缺「无网关身份」前置  → test_gateway_header_bypasses_token_restriction 失败
"""

import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import USER_A

# 不适合泛化调用：需要文件上传 / 返回文件流，无法用空 body 探测
SKIP_ROOT_PREFIXES = ("/api/upload", "/api/bill/export")
READ_METHODS = {"GET", "HEAD", "OPTIONS"}
WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}
# 拦截码：403 = Token 分支明确拒绝；401 = Token 未被识别时按「网关无身份」拒绝
BLOCKED_CODES = (401, 403)
# Token 写方法豁免（permissions.token_write_allowed）：MCP 的 JSON-RPC
# （initialize / tools/list / tools/call）全部走 POST 且工具集只读，是唯一
# 允许 Token 写方法到达路由的端点——请求**没被拦下**是预期行为，不计入越权。
TOKEN_WRITABLE_EXEMPT = {("POST", "/api/mcp")}


@pytest.fixture()
def gw_api(tmp_path, monkeypatch):
    """网关模式 + 真实中间件栈 + 全部业务路由的测试客户端

    权限中间件按 TRIM_PKGVAR 决定网关模式，故须 reload config/context/
    permissions/middleware 四个模块；退出时 undo + 再 reload 防污染其他用例。
    """
    monkeypatch.setenv("TRIM_PKGVAR", str(tmp_path))
    monkeypatch.setenv("SCHEDULER_ENABLED", "0")

    import app.config as config
    import app.core.context as context
    import app.core.middleware as middleware
    import app.core.permissions as permissions

    for mod in (config, context, permissions, middleware):
        importlib.reload(mod)

    from app.api import (
        ai,
        asset,
        audit,
        automation,
        bill,
        budget,
        category,
        family,
        forecast,
        ledger,
        loans,
        nas,
        nl_query,
        notify,
        reimb,
        savings,
        settings,
        stat,
        tokens,
        update,
        upload,
    )
    from app.core.handlers import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)
    for router in (
        upload.router,
        nas.router,
        bill.router,
        budget.router,
        asset.router,
        audit.router,
        reimb.router,
        savings.router,
        category.router,
        ledger.router,
        loans.router,
        family.router,
        stat.router,
        tokens.router,
        forecast.router,
        settings.router,
        update.router,
        automation.router,
        notify.router,
        notify.config_router,
        ai.router,
        nl_query.router,
    ):
        app.include_router(router)
    middleware.add_app_middlewares(app)

    yield TestClient(app), permissions

    monkeypatch.undo()
    for mod in (config, context, permissions, middleware):
        importlib.reload(mod)


def _make_token() -> str:
    from app.services import token_service

    payload = type("P", (), {"name": "扫测"})
    return token_service.create_token(payload, USER_A)["token"]


def _enumerate(permissions):
    """按 Token 准入结论给路径分类：只读可达 / 受限（写方法或管理面）"""
    from app.main import app as real_app

    readable, writable = set(), set()
    for path, ops in real_app.openapi().get("paths", {}).items():
        root = permissions.strip_api_prefix(path)
        if any(root.startswith(p) for p in SKIP_ROOT_PREFIXES):
            continue
        if "{" in root:  # 路径参数端点跳过（需要真实 id，无法泛化调用）
            continue
        for method in (m.upper() for m in ops):
            if method in WRITE_METHODS or permissions.is_admin_surface(root, method):
                writable.add((method, root))
            elif method in READ_METHODS:
                readable.add(root)
    return sorted(readable), sorted(writable)


def test_token_read_surface_stays_healthy(db, gw_api):
    """只读面：Token 可读的每个端点都不得 5xx，也不得被 401/403 误伤"""
    client, permissions = gw_api
    auth = {"Authorization": f"Bearer {_make_token()}"}

    readable, writable = _enumerate(permissions)
    # 反恒真：确认路径枚举真的生效（漏匹配会让下面的循环空转）
    assert len(readable) >= 30, f"只读端点枚举过少（{len(readable)}），路径匹配可能失效"
    assert len(writable) >= 30, f"受限端点枚举过少（{len(writable)}），策略表可能失效"

    problems = []
    for root in readable:
        res = client.get(root, headers=auth)
        if res.status_code >= 500:
            problems.append(f"{root} -> {res.status_code} {res.text[:200]}")
        elif res.status_code in BLOCKED_CODES:
            problems.append(
                f"{root} -> {res.status_code}（只读端点被误伤）{res.text[:160]}"
            )
    assert not problems, "Token 只读面异常：\n" + "\n".join(problems)


def test_token_cannot_reach_any_restricted_endpoint(db, gw_api):
    """受限面：所有写方法与管理面端点对 Token 一律被拦（不得 2xx/3xx/5xx）

    拦截码 403 表示 Token 分支明确拒绝（只读限制 / 管理面专属），401 表示
    Token 未被识别而落到「网关模式下无身份头 = 未认证」。两者都表示请求未
    到达路由，安全性等价，故只断言「没被放行」。
    """
    client, permissions = gw_api
    auth = {"Authorization": f"Bearer {_make_token()}"}

    _, writable = _enumerate(permissions)
    assert writable, "受限端点枚举为空"

    leaked = []
    for method, root in writable:
        if (method, root) in TOKEN_WRITABLE_EXEMPT:
            continue
        res = client.request(method, root, headers=auth, json={})
        if res.status_code not in BLOCKED_CODES:
            leaked.append(f"{method} {root} -> {res.status_code} {res.text[:160]}")
    assert not leaked, "Token 越权可达的端点：\n" + "\n".join(leaked)


def test_gateway_header_bypasses_token_restriction(db, gw_api):
    """网关可信头优先：带管理员网关头时 Token 不参与，写端点不再受只读限制

    这是 2026-09-19 修复的缺陷（Token 分支缺「无网关身份」前置条件）的回归：
    缺陷版下带网关头 + 任意 Token 的写请求会被误判为「Token 只读」而 403。
    """
    client, permissions = gw_api
    token = _make_token()
    admin = {
        "X-Trim-Userid": USER_A,
        "X-Trim-Isadmin": "true",
        "Authorization": f"Bearer {token}",
    }

    _, writable = _enumerate(permissions)
    misjudged = []
    for method, root in writable:
        res = client.request(method, root, headers=admin, json={})
        # 关键判据：不得出现「Token 只读」文案 —— 那是 Token 分支误介入的信号
        if res.status_code in BLOCKED_CODES and "只读" in res.text:
            misjudged.append(f"{method} {root} -> {res.text[:120]}")
    assert not misjudged, "网关头在场却被 Token 只读限制误拦：\n" + "\n".join(misjudged)
