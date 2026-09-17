"""权限控制中间件测试：身份认证（无头 401）+ 管理面策略表 + 双层防御一致性

中间件（core/permissions.py）在路由分发前做两级判定：网关模式缺失身份头一律
401；管理面对非管理员 403。路由层的 deps.require_admin 是第二道防线，两者对
同一请求必须给出完全一致的统一响应体（401→code=10006、403→code=10002），
前端无需区分拦截来源。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import ai
from app.core.context import GatewayUser
from app.core.handlers import register_exception_handlers
from app.core.middleware import add_app_middlewares
from app.core.permissions import (
    ADMIN_ONLY_MSG,
    UNAUTHENTICATED_MSG,
    access_rejection,
    strip_api_prefix,
)

PREFIX = "/app/fn-finstat"

ADMIN = GatewayUser(user_id="10001", is_admin=True)
USER = GatewayUser(user_id="10002")
LOCAL = GatewayUser()

FORBIDDEN_BODY = {"code": 10002, "msg": ADMIN_ONLY_MSG, "data": None}
UNAUTHORIZED_BODY = {"code": 10006, "msg": UNAUTHENTICATED_MSG, "data": None}


@pytest.fixture(autouse=True)
def fixed_guard_config(monkeypatch):
    """固定向导前缀与运行形态，单测不随本地 .env.dev / 部署环境取值漂移

    - permissions / deps 两处 from-import 各持有一份形态开关，需分别固定；
    - 来源校验中间件（middleware.SourceGuardMiddleware）在完整中间件栈内激活，
      TestClient 默认 Host=testserver 不在回环白名单，放行之以免本文件所有
      端到端用例被来源拦截（其判定矩阵在 test_source_guard.py 单测）。
    """
    monkeypatch.setattr("app.core.permissions.API_BASE_PATH", PREFIX)
    monkeypatch.setattr("app.core.permissions.IS_FNOS", False)
    monkeypatch.setattr("app.core.permissions.ALLOW_HEADERLESS", False)
    monkeypatch.setattr("app.api.deps.IS_FNOS", False)
    monkeypatch.setattr("app.api.deps.ALLOW_HEADERLESS", False)
    monkeypatch.setattr("app.core.middleware.IS_FNOS", False)
    monkeypatch.setattr("app.core.middleware.HOST_IS_WILDCARD", False)
    monkeypatch.setattr(
        "app.core.middleware.ALLOWED_HOSTS",
        frozenset({"testserver", "127.0.0.1", "localhost", "::1"}),
    )


# ---- 策略表：管理面识别与访问判定 ----


@pytest.mark.parametrize(
    ("user", "path", "method", "expected"),
    [
        # ---- NAS：写管理员、读公开 ----
        (USER, "/api/nas/config", "PUT", 403),
        (USER, "/api/nas/config", "GET", None),
        (ADMIN, "/api/nas/config", "PUT", None),
        (USER, PREFIX + "/api/nas/config", "PUT", 403),
        # ---- 设置：认领 / 数据库 / 日志 / 备份恢复 ----
        (USER, "/api/settings/user/claim", "POST", 403),
        (USER, "/api/settings/database/test", "POST", 403),
        (USER, "/api/settings/database/migrate", "POST", 403),
        (USER, "/api/settings/logs", "GET", 403),
        (USER, "/api/settings/logs/download", "GET", 403),
        (USER, "/api/settings/backup", "GET", 403),
        (USER, "/api/settings/restore", "POST", 403),
        (LOCAL, "/api/settings/logs", "GET", None),
        # ---- 自动化：写管理员、列表与运行历史可读 ----
        (USER, "/api/settings/automation/nas_watch/run", "POST", 403),
        (USER, "/api/settings/automation/nas_watch/toggle", "POST", 403),
        (USER, "/api/settings/automation/nas_watch", "PUT", 403),
        (USER, "/api/settings/automation", "GET", None),
        (USER, "/api/settings/automation/nas_watch/runs", "GET", None),
        # ---- AI / 分类 ----
        (USER, "/api/ai/config", "PUT", 403),
        (USER, "/api/ai/test", "POST", 403),
        (USER, "/api/ai/report/generate", "POST", None),
        (USER, "/api/category", "POST", 403),
        (USER, "/api/category/3", "PUT", 403),
        (USER, "/api/category/3", "DELETE", 403),
        (USER, "/api/category/3", "GET", None),
        # ---- 分类学习规则：写管理员、列表可读 ----
        (USER, "/api/settings/learned-rules/3", "PUT", 403),
        (USER, "/api/settings/learned-rules/3", "DELETE", 403),
        (USER, "/api/settings/learned-rules", "GET", None),
        # ---- 普通接口与非 API 面不受影响 ----
        (USER, "/api/bill/list", "GET", None),
        (USER, "/api/upload/wechat", "POST", None),
        (USER, PREFIX + "/api/bill/list", "GET", None),
        (USER, "/docs", "GET", None),
        (USER, "/openapi.json", "GET", None),
        # 更新检查是只读远端公开信息、不碰本机数据，普通账号即可用（非管理面）
        (USER, "/api/update/check", "GET", None),
        (USER, PREFIX + "/api/update/check", "GET", None),
        # ---- 边界：前缀必须完整跟随 "/" 才剥除；管理面正则不误伤相近路径 ----
        (USER, "/app/fn-finstata/api/nas/config", "PUT", None),
        (USER, "/api/settings/automationx/nas_watch/run", "POST", None),
    ],
)
def test_access_rejection_matrix(user, path, method, expected):
    """独立部署形态（IS_FNOS=False）：无头放行，管理面拦截 403"""
    assert access_rejection(user, path, method) == expected


# ---- 网关模式：缺失身份头一律 401（信任边界收紧，见评审报告 H-1）----


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/api/bill/list", "GET"),  # 普通接口同样拒绝：数据隔离依赖 user_id 过滤
        ("/api/settings/backup", "GET"),  # 管理面
        (PREFIX + "/api/settings/backup", "GET"),  # 带向导前缀
    ],
)
def test_fnos_mode_headerless_rejected(monkeypatch, path, method):
    """网关模式且未开逃生开关：无头请求 = 未认证，对全部路径回 401"""
    monkeypatch.setattr("app.core.permissions.IS_FNOS", True)
    assert access_rejection(LOCAL, path, method) == 401


def test_fnos_mode_logged_in_user_still_governed_by_admin_rules(monkeypatch):
    """网关模式收紧不影响既有账号语义：登录账号放行普通面、管理面仍看管理员"""
    monkeypatch.setattr("app.core.permissions.IS_FNOS", True)
    assert access_rejection(USER, "/api/bill/list", "GET") is None
    assert access_rejection(USER, "/api/settings/backup", "GET") == 403
    assert access_rejection(ADMIN, "/api/settings/backup", "GET") is None


def test_fnos_mode_escape_hatch_restores_headerless(monkeypatch):
    """逃生开关 FNOS_ALLOW_HEADERLESS=1：临时恢复「无头 = 单机用户」旧行为"""
    monkeypatch.setattr("app.core.permissions.IS_FNOS", True)
    monkeypatch.setattr("app.core.permissions.ALLOW_HEADERLESS", True)
    assert access_rejection(LOCAL, "/api/bill/list", "GET") is None
    assert access_rejection(LOCAL, "/api/settings/backup", "GET") is None


def test_strip_api_prefix_boundaries():
    assert strip_api_prefix(f"{PREFIX}/api/bill/list") == "/api/bill/list"
    assert strip_api_prefix("/api/bill/list") == "/api/bill/list"
    # 无尾斜杠的前缀本身不剥（对应 main.py 的 302 重定向路由）
    assert strip_api_prefix(PREFIX) == PREFIX
    # 仅前缀字符串相似不剥除
    assert strip_api_prefix("/app/fn-finstata/api/x") == "/app/fn-finstata/api/x"


# ---- 端到端：真实路由 + 完整中间件栈 ----


def make_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ai.router)
    add_app_middlewares(app)
    return TestClient(app)


def test_middleware_blocks_nonadmin_before_config_write(monkeypatch):
    """非管理员写 AI 配置：中间件直接 403，不进入路由/配置写入"""
    called = []
    monkeypatch.setattr("app.api.ai.save_ai_settings", lambda s: called.append(s))
    resp = make_client().put(
        "/api/ai/config",
        json={"model": "evil-model"},
        headers={"X-Trim-Userid": "10002"},
    )
    assert resp.status_code == 403
    assert resp.json() == FORBIDDEN_BODY
    assert called == []
    # 拦截响应同样经过观测与安全头中间件
    assert len(resp.headers["x-request-id"]) == 16
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_middleware_blocks_nonadmin_on_prefixed_path():
    """带向导前缀的管理面路径同样被拦截（前缀剥除后命中策略表）"""
    resp = make_client().put(
        f"{PREFIX}/api/ai/config", json={}, headers={"X-Trim-Userid": "10002"}
    )
    assert resp.status_code == 403
    assert resp.json() == FORBIDDEN_BODY


def test_admin_and_local_mode_pass_middleware(db):
    """通过中间件与路由守卫后进入业务层（路由级会话依赖需 init_db，故挂 db 夹具）"""
    client = make_client()
    admin = client.put(
        "/api/ai/config",
        json={},
        headers={"X-Trim-Userid": "10001", "X-Trim-Isadmin": "true"},
    )
    assert admin.status_code == 200
    assert admin.json()["code"] == 0
    # 无身份头 = 本地单机模式，全量放行
    local = client.put("/api/ai/config", json={})
    assert local.status_code == 200


def test_dependency_guard_body_matches_middleware(db):
    """仅挂路由守卫（无中间件）的 403 与中间件拦截响应体完全一致"""
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ai.router)
    resp = TestClient(app).put(
        "/api/ai/config", json={}, headers={"X-Trim-Userid": "10002"}
    )
    assert resp.status_code == 403
    assert resp.json() == FORBIDDEN_BODY


# ---- 端到端：网关模式无头 401（中间件与路由守卫两层一致）----


def test_fnos_headerless_request_gets_401_unified_body(monkeypatch, db):
    """网关模式无头请求被中间件拦截为统一 401；带上身份头后同一请求放行"""
    monkeypatch.setattr("app.core.permissions.IS_FNOS", True)
    monkeypatch.setattr("app.api.deps.IS_FNOS", True)
    client = make_client()
    resp = client.get("/api/ai/config")
    assert resp.status_code == 401
    assert resp.json() == UNAUTHORIZED_BODY
    # 拦截响应同样带观测头（安全头/请求 ID 链路不受新增拦截影响）
    assert len(resp.headers["x-request-id"]) == 16
    ok = client.get("/api/ai/config", headers={"X-Trim-Userid": "10002"})
    assert ok.status_code == 200


def test_fnos_escape_hatch_headerless_put_admin(monkeypatch, db):
    """逃生开关开启：无头请求等同单机用户，可写管理面（旧行为，仅排障用）"""
    monkeypatch.setattr("app.core.permissions.IS_FNOS", True)
    monkeypatch.setattr("app.api.deps.IS_FNOS", True)
    monkeypatch.setattr("app.core.permissions.ALLOW_HEADERLESS", True)
    monkeypatch.setattr("app.api.deps.ALLOW_HEADERLESS", True)
    resp = make_client().put("/api/ai/config", json={})
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


def test_unauthenticated_guard_body_matches_middleware(monkeypatch, db):
    """仅挂路由守卫（无中间件）的 401 与中间件拦截响应体完全一致"""
    monkeypatch.setattr("app.api.deps.IS_FNOS", True)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(ai.router)
    resp = TestClient(app).put("/api/ai/config", json={})
    assert resp.status_code == 401
    assert resp.json() == UNAUTHORIZED_BODY


def test_require_admin_raises_unauthenticated_in_fnos_mode(monkeypatch):
    """路由守卫直接单测：网关模式无头 → UnauthorizedError（401/10006）"""
    from app.api.deps import require_admin
    from app.core.errors import UnauthorizedError

    monkeypatch.setattr("app.api.deps.IS_FNOS", True)
    with pytest.raises(UnauthorizedError) as exc:
        require_admin(LOCAL)
    assert exc.value.http_status == 401
    assert exc.value.code == 10006
    assert str(exc.value) == UNAUTHENTICATED_MSG
