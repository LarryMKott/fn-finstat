"""独立部署来源校验中间件测试（core/middleware.py::SourceGuardMiddleware）

覆盖 Host 白名单（防 DNS rebinding）与写方法 Origin 同源校验（防跨站 CSRF）
的判定矩阵，以及 fnOS 网关模式整体跳过。生产装配见 add_app_middlewares，
此处按需搭建最小探针应用。
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.handlers import register_exception_handlers
from app.core.middleware import (
    UNTRUSTED_SOURCE_MSG,
    add_app_middlewares,
)

FORBIDDEN_BODY = {"code": 10002, "msg": UNTRUSTED_SOURCE_MSG, "data": None}


@pytest.fixture(autouse=True)
def standalone_guard(monkeypatch):
    """固定独立部署形态与回环白名单，单测不随本机 HOST / TRIM_PKGVAR 漂移"""
    monkeypatch.setattr("app.core.middleware.IS_FNOS", False)
    monkeypatch.setattr("app.core.middleware.HOST_IS_WILDCARD", False)
    monkeypatch.setattr("app.core.middleware.ALLOW_HOSTNAME_WHEN_WILDCARD", False)
    monkeypatch.setattr(
        "app.core.middleware.ALLOWED_HOSTS",
        frozenset({"127.0.0.1", "localhost", "::1"}),
    )


def make_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/probe")
    def probe():
        return {"ok": True}

    @app.post("/submit")
    def submit():
        return {"ok": True}

    add_app_middlewares(app)
    return TestClient(app)


# ---- Host 白名单（全部请求，防 DNS rebinding）----


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:8090",
        "localhost:8090",
        "[::1]:8090",  # IPv6 字面量带端口
        "127.0.0.1",  # 不带端口
        "LOCALHOST:8090",  # 大小写不敏感
    ],
)
def test_loopback_hosts_allowed(host):
    resp = make_client().get("/probe", headers={"Host": host})
    assert resp.status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "evil.com",
        "evil.com:8090",  # rebinding 后端口与真实服务一致也拦
        "127.0.0.1.evil.com",  # 子域相似不误放
        "localhost.evil.com",
    ],
)
def test_foreign_hosts_blocked_on_get(host):
    """rebinding 把攻击域名解析到 127.0.0.1 后，Host 仍是攻击域名——白名单拦下"""
    resp = make_client().get("/probe", headers={"Host": host})
    assert resp.status_code == 403
    assert resp.json() == FORBIDDEN_BODY


def test_host_any_port_allowed_for_whitelisted_name():
    """白名单按主机名判定，不比较端口（浏览器总是连到真实服务端口）"""
    resp = make_client().get("/probe", headers={"Host": "127.0.0.1:1"})
    assert resp.status_code == 200


def test_guard_skipped_in_fnos_mode(monkeypatch):
    """网关模式整体跳过：Host/Origin 校验由网关负责，iframe 跨子域不被误拦"""
    monkeypatch.setattr("app.core.middleware.IS_FNOS", True)
    client = make_client()
    assert client.get("/probe", headers={"Host": "evil.com"}).status_code == 200
    assert (
        client.post(
            "/submit",
            headers={"Host": "evil.com", "Origin": "https://evil.com"},
        ).status_code
        == 200
    )


def test_wildcard_host_still_blocks_foreign_domains(monkeypatch):
    """HOST=0.0.0.0 等通配地址：白名单取「回环 + 本机全部网卡 IP」，域名一律拒绝

    修复 M14-2：通配绑定不再整体关闭校验。局域网用户按 IP 访问照常放行
    （该 IP 已在白名单里），而 DNS rebinding 必须使用攻击者域名 → 被拦。
    """
    monkeypatch.setattr("app.core.middleware.HOST_IS_WILDCARD", True)
    monkeypatch.setattr("app.core.middleware.ALLOW_HOSTNAME_WHEN_WILDCARD", False)
    monkeypatch.setattr(
        "app.core.middleware.ALLOWED_HOSTS",
        frozenset({"127.0.0.1", "localhost", "::1", "192.168.1.10"}),
    )
    client = make_client()

    # 局域网按 IP 访问：放行（白名单含该 IP）
    assert (
        client.get("/probe", headers={"Host": "192.168.1.10:8090"}).status_code == 200
    )
    # 攻击者域名：拒绝（DNS rebinding 的入口）
    for evil in ("attacker.example.com", "evil.com:8090", "localhost.evil.com"):
        resp = client.get("/probe", headers={"Host": evil})
        assert resp.status_code == 403, f"{evil} 未被拦截"
        assert resp.json() == FORBIDDEN_BODY

    # 跨站写请求仍被 Origin 校验拦下
    evil_post = client.post(
        "/submit",
        headers={"Host": "192.168.1.10:8090", "Origin": "https://evil.com"},
    )
    assert evil_post.status_code == 403


def test_wildcard_host_rejects_dns_rebinding_full_path(monkeypatch):
    """修复后的完整危害路径复测：攻击页面与伪造 Host 同源 → 也被拦

    对应安全报告 M14-2 的危害路径：浏览器地址栏是 attacker.example.com，
    恶意 JS 同源请求。修复前 Host 白名单关闭 + Origin 同源 → 双双通过；
    修复后 Host 不在白名单 → 403。
    """
    monkeypatch.setattr("app.core.middleware.HOST_IS_WILDCARD", True)
    monkeypatch.setattr("app.core.middleware.ALLOW_HOSTNAME_WHEN_WILDCARD", False)
    monkeypatch.setattr(
        "app.core.middleware.ALLOWED_HOSTS", frozenset({"127.0.0.1", "localhost"})
    )
    client = make_client()
    resp = client.get(
        "/probe",
        headers={
            "Host": "attacker.example.com",
            "Origin": "http://attacker.example.com",
        },
    )
    assert resp.status_code == 403


def test_wildcard_host_escape_hatch_restores_hostname_access(monkeypatch):
    """逃逸开关：FNOS_ALLOW_HOSTNAME_WHEN_WILDCARD=1 时域名放行（旧行为）

    供确有 mDNS / 主机名访问需求的用户使用；开启即扩大信任面。
    """
    monkeypatch.setattr("app.core.middleware.HOST_IS_WILDCARD", True)
    monkeypatch.setattr("app.core.middleware.ALLOW_HOSTNAME_WHEN_WILDCARD", True)
    monkeypatch.setattr(
        "app.core.middleware.ALLOWED_HOSTS", frozenset({"127.0.0.1"})
    )
    client = make_client()
    assert client.get("/probe", headers={"Host": "nas.local"}).status_code == 200


def test_loopback_names_still_allowed_under_wildcard(monkeypatch):
    """通配绑定时 localhost / 回环 IP 仍在白名单（本地调试不受影响）"""
    monkeypatch.setattr("app.core.middleware.HOST_IS_WILDCARD", True)
    monkeypatch.setattr("app.core.middleware.ALLOW_HOSTNAME_WHEN_WILDCARD", False)
    monkeypatch.setattr(
        "app.core.middleware.ALLOWED_HOSTS",
        frozenset({"127.0.0.1", "localhost", "::1"}),
    )
    client = make_client()
    for host in ("127.0.0.1:8090", "localhost:8090", "[::1]:8090"):
        assert client.get("/probe", headers={"Host": host}).status_code == 200


def test_malformed_host_rejected(monkeypatch):
    """非法 Host（解析失败）一律拒绝，不因解析异常而放行"""
    monkeypatch.setattr("app.core.middleware.HOST_IS_WILDCARD", True)
    monkeypatch.setattr("app.core.middleware.ALLOW_HOSTNAME_WHEN_WILDCARD", False)
    client = make_client()
    for bad in ("127.0.0.1:99999", "a b", "[::1"):
        assert client.get("/probe", headers={"Host": bad}).status_code == 403


# ---- 写方法 Origin 同源校验（防跨站 CSRF）----


def test_post_same_origin_allowed():
    resp = make_client().post(
        "/submit",
        headers={"Host": "127.0.0.1:8090", "Origin": "http://127.0.0.1:8090"},
    )
    assert resp.status_code == 200


def test_post_cross_origin_blocked():
    resp = make_client().post(
        "/submit",
        headers={"Host": "127.0.0.1:8090", "Origin": "https://evil.com"},
    )
    assert resp.status_code == 403
    assert resp.json() == FORBIDDEN_BODY


def test_post_origin_port_mismatch_blocked():
    resp = make_client().post(
        "/submit",
        headers={"Host": "127.0.0.1:8090", "Origin": "http://127.0.0.1:9999"},
    )
    assert resp.status_code == 403


def test_post_without_origin_allowed():
    """curl / 脚本 / Service Worker 不带 Origin：放行（非浏览器 CSRF 面）"""
    resp = make_client().post("/submit", headers={"Host": "127.0.0.1:8090"})
    assert resp.status_code == 200


def test_get_cross_origin_allowed():
    """GET 不做 Origin 校验：跨站读取本就被同源策略阻止，无需拦截"""
    resp = make_client().get(
        "/probe",
        headers={"Host": "127.0.0.1:8090", "Origin": "https://evil.com"},
    )
    assert resp.status_code == 200


def test_origin_default_port_normalized():
    """Origin 省略默认端口与 Host 显式 80 视为同源（反向代理常见形态）"""
    resp = make_client().post(
        "/submit",
        headers={"Host": "127.0.0.1:80", "Origin": "http://127.0.0.1"},
    )
    assert resp.status_code == 200


def test_blocked_request_still_gets_observability_headers():
    """被来源拦截的 403 也经过外层观测/安全头中间件（统一响应体验证）"""
    resp = make_client().get("/probe", headers={"Host": "evil.com"})
    assert resp.status_code == 403
    assert len(resp.headers["x-request-id"]) == 16
    assert resp.headers["x-content-type-options"] == "nosniff"
