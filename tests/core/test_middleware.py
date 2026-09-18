"""HTTP 中间件与请求级数据库会话依赖的测试

覆盖两块新基建（生产装配见 app/main.py，测试装配在此按需搭建探针应用）：
- core/middleware.py：请求 ID 透传/生成、耗时与安全响应头、慢请求日志
- api/deps.request_db_session：同一请求内 get_db 复用同一会话；
  无请求上下文（后台线程）时逐调用新建会话的旧行为保持不变
"""

import logging

from fastapi import Depends, FastAPI

from app.api.deps import request_db_session
from app.core.context import current_request_id, request_id_var
from app.core.handlers import register_exception_handlers
from app.core.middleware import (
    ObservabilityMiddleware,
    SecurityHeadersMiddleware,
    SLOW_REQUEST_MS,
)
from app.db.base import get_db
from app.db.models import Category
from fastapi.testclient import TestClient

X_REQUEST_ID = "x-request-id"


def make_probe_app() -> FastAPI:
    """最小探针应用：注册与生产一致的中间件与异常处理器，挂观测用路由"""
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(ObservabilityMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/probe")
    def probe():
        return {"ok": True, "rid": current_request_id()}

    @app.get("/boom")
    def boom():
        raise RuntimeError("探针异常")

    return app


# ---- 请求 ID 与观测响应头 ----


def test_request_id_generated_and_exposed():
    client = TestClient(make_probe_app())
    resp = client.get("/probe")
    assert resp.status_code == 200
    rid = resp.headers[X_REQUEST_ID]
    assert len(rid) == 16 and all(c in "0123456789abcdef" for c in rid)
    # 端点内经 ContextVar 读到同一请求 ID
    assert resp.json()["rid"] == rid
    assert resp.headers["x-process-time-ms"].isdigit()
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"


def test_request_id_unique_per_request():
    client = TestClient(make_probe_app())
    rid1 = client.get("/probe").headers[X_REQUEST_ID]
    rid2 = client.get("/probe").headers[X_REQUEST_ID]
    assert rid1 != rid2


def test_upstream_request_id_honored():
    client = TestClient(make_probe_app())
    resp = client.get("/probe", headers={X_REQUEST_ID: "gw-trace-001"})
    assert resp.headers[X_REQUEST_ID] == "gw-trace-001"


def test_malicious_request_id_regenerated():
    """不合法的请求 ID 不回显（防响应头注入），重新生成合法 ID"""
    client = TestClient(make_probe_app())
    for evil in ("abc;def", "x" * 100, "a b c"):
        resp = client.get("/probe", headers={X_REQUEST_ID: evil})
        rid = resp.headers[X_REQUEST_ID]
        assert rid != evil
        assert len(rid) == 16


def test_unhandled_exception_keeps_headers_and_request_id(caplog):
    """未处理异常：统一响应体 + 观测头不丢，异常日志带同一请求 ID"""
    client = TestClient(make_probe_app(), raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="app.core.handlers"):
        resp = client.get("/boom")
    assert resp.status_code == 500
    assert resp.json() == {
        "code": 50000,
        "msg": "服务器内部错误，请稍后重试",
        "data": None,
    }
    rid = resp.headers[X_REQUEST_ID]
    assert len(rid) == 16
    assert f"请求ID {rid}" in caplog.text


def test_slow_request_logged(caplog, monkeypatch):
    monkeypatch.setattr("app.core.middleware.SLOW_REQUEST_MS", 0)
    client = TestClient(make_probe_app())
    with caplog.at_level(logging.INFO, logger="app.core.middleware"):
        resp = client.get("/probe")
    assert resp.status_code == 200
    assert "慢请求" in caplog.text
    assert resp.headers[X_REQUEST_ID] in caplog.text


def test_slow_log_quiet_for_fast_requests(caplog):
    assert SLOW_REQUEST_MS >= 500  # 阈值不被误调低时快速请求不产生慢日志
    client = TestClient(make_probe_app())
    with caplog.at_level(logging.INFO, logger="app.core.middleware"):
        client.get("/probe")
    assert "慢请求" not in caplog.text


def test_request_id_var_reset_after_request():
    client = TestClient(make_probe_app())
    client.get("/probe", headers={X_REQUEST_ID: "abc"})
    # 中间件 finally 复位 ContextVar，不向后续代码泄漏上一请求的 ID
    assert request_id_var.get() == ""


# ---- 请求级数据库会话依赖 ----


def make_db_probe_app() -> FastAPI:
    """挂 router 级会话依赖的探针：验证同一请求内 get_db 复用同一会话"""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/sessions", dependencies=[Depends(request_db_session)])
    def sessions():
        with get_db() as s1:
            with get_db() as s2:
                return {"same": s1 is s2}

    return app


def test_request_db_session_reused_within_request(db):
    client = TestClient(make_db_probe_app())
    resp = client.get("/sessions")
    assert resp.status_code == 200
    assert resp.json()["same"] is True


def test_get_db_without_request_context_opens_fresh_sessions(db):
    """后台线程语义不变：无请求上下文时每次 get_db 独立建会话（用后即关）"""
    with get_db() as s1:
        with get_db() as s2:
            assert s1 is not s2


def test_request_session_writes_commit_and_close(db):
    """经请求级会话写入后数据落库；会话绑定在请求结束后复位"""
    from sqlalchemy import select

    from app.db.base import new_session
    from app.db.dao.category_dao import CategoryDAO

    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/spawn-category", dependencies=[Depends(request_db_session)])
    def spawn_category():
        CategoryDAO.create("探针分类")
        with get_db() as session:
            names = session.scalars(select(Category.name)).all()
        return {"names": names}

    client = TestClient(app)
    resp = client.post("/spawn-category")
    assert resp.status_code == 200
    assert "探针分类" in resp.json()["names"]
    # 请求外的新会话能看到已提交数据（依赖结束时会话已提交并关闭）
    with new_session() as fresh:
        assert "探针分类" in fresh.scalars(select(Category.name)).all()
