"""权限控制中间件测试：管理面策略表 + 端到端拦截与路由守卫的双层一致性

中间件（core/permissions.py）在路由分发前按策略表拦截管理面；路由层的
deps.require_admin 是第二道防线。两者对同一请求必须给出完全一致的 403
统一响应体（code=10002），前端无需区分拦截来源。
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
    permits,
    strip_api_prefix,
)

PREFIX = "/app/fn-finstat"

ADMIN = GatewayUser(user_id="10001", is_admin=True)
USER = GatewayUser(user_id="10002")
LOCAL = GatewayUser()

FORBIDDEN_BODY = {"code": 10002, "msg": ADMIN_ONLY_MSG, "data": None}


@pytest.fixture(autouse=True)
def fixed_api_prefix(monkeypatch):
    """固定向导前缀，单测不随本地 .env.dev 的 API_BASE_PATH 取值漂移"""
    monkeypatch.setattr("app.core.permissions.API_BASE_PATH", PREFIX)


# ---- 策略表：管理面识别与放行判定 ----


@pytest.mark.parametrize(
    ("user", "path", "method", "expected"),
    [
        # ---- NAS：写管理员、读公开 ----
        (USER, "/api/nas/config", "PUT", False),
        (USER, "/api/nas/config", "GET", True),
        (ADMIN, "/api/nas/config", "PUT", True),
        (USER, PREFIX + "/api/nas/config", "PUT", False),
        # ---- 设置：认领 / 数据库 / 日志 / 备份恢复 ----
        (USER, "/api/settings/user/claim", "POST", False),
        (USER, "/api/settings/database/test", "POST", False),
        (USER, "/api/settings/database/migrate", "POST", False),
        (USER, "/api/settings/logs", "GET", False),
        (USER, "/api/settings/logs/download", "GET", False),
        (USER, "/api/settings/backup", "GET", False),
        (USER, "/api/settings/restore", "POST", False),
        (LOCAL, "/api/settings/logs", "GET", True),
        # ---- 自动化：写管理员、列表与运行历史可读 ----
        (USER, "/api/settings/automation/nas_watch/run", "POST", False),
        (USER, "/api/settings/automation/nas_watch/toggle", "POST", False),
        (USER, "/api/settings/automation/nas_watch", "PUT", False),
        (USER, "/api/settings/automation", "GET", True),
        (USER, "/api/settings/automation/nas_watch/runs", "GET", True),
        # ---- AI / 分类 ----
        (USER, "/api/ai/config", "PUT", False),
        (USER, "/api/ai/test", "POST", False),
        (USER, "/api/ai/report/generate", "POST", True),
        (USER, "/api/category", "POST", False),
        (USER, "/api/category/3", "PUT", False),
        (USER, "/api/category/3", "DELETE", False),
        (USER, "/api/category/3", "GET", True),
        # ---- 普通接口与非 API 面不受影响 ----
        (USER, "/api/bill/list", "GET", True),
        (USER, "/api/upload/wechat", "POST", True),
        (USER, PREFIX + "/api/bill/list", "GET", True),
        (USER, "/docs", "GET", True),
        (USER, "/openapi.json", "GET", True),
        # ---- 边界：前缀必须完整跟随 "/" 才剥除；管理面正则不误伤相近路径 ----
        (USER, "/app/fn-finstata/api/nas/config", "PUT", True),
        (USER, "/api/settings/automationx/nas_watch/run", "POST", True),
    ],
)
def test_permits_matrix(user, path, method, expected):
    assert permits(user, path, method) is expected


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
