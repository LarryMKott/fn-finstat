"""用户账单目录授权接口测试

覆盖三种运行场景：
1. 本地开发（无 trim socket/token）—— available=False，理由文案给出
2. 飞牛环境 + trim 网关正常 —— folders 返回授权列表
3. 飞牛环境 + trim 网关异常（不可用 / 被拒绝） —— available=False，不抛 5xx

通过 monkeypatch 注入 `is_trim_runtime` 状态与 `_request` 行为，
避免真打 Unix Socket。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import nas_authorization_service, trim_gateway


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def patch_shared(monkeypatch, paths=None, exc=None):
    """给「应用共享目录」查询打桩，避免测试真的去连 /var/run 下的 Unix Socket

    `get_user_authorization` 在个人目录查询成功后会再查一次共享目录；
    不打桩的话这条分支会落到真实 socket 调用，在 CI/Windows 上既慢又噪声大。
    """

    def fake(*, app_name):
        if exc is not None:
            raise exc
        return trim_gateway.SharedAccessibleFolders(paths=list(paths or []))

    monkeypatch.setattr(trim_gateway, "get_shared_accessible_folders", fake)


# --------------------------- 单元测试 ---------------------------


def test_uid_from_user_id_stable():
    """同一字符串多次派生必须稳定，正整数"""
    a = trim_gateway.uid_from_user_id("alice")
    b = trim_gateway.uid_from_user_id("alice")
    assert a == b
    assert a > 0
    c = trim_gateway.uid_from_user_id("bob")
    assert c != a  # 不同用户基本不会撞（同长度小概率，crc32 32 位足够散）


def test_uid_from_empty_user_id_uses_one():
    """空串不允许映射到 0（与"未登录"歧义）"""
    assert trim_gateway.uid_from_user_id("") == 1


def test_is_trim_runtime_false_without_socket(monkeypatch):
    """socket 文件不存在时一律 False"""
    import os
    monkeypatch.setattr(os.path, "exists", lambda _p: False)
    monkeypatch.setenv("TRIM_API_TOKEN", "tok")
    assert trim_gateway.is_trim_runtime() is False


def test_is_trim_runtime_false_without_token(monkeypatch):
    """token 缺失时 False"""
    import os
    monkeypatch.setattr(os.path, "exists", lambda _p: True)
    monkeypatch.delenv("TRIM_API_TOKEN", raising=False)
    assert trim_gateway.is_trim_runtime() is False


def test_get_user_authorization_locally_falls_back(monkeypatch):
    """本地场景：available=False，reason 文案给出"""
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: False)
    user = type("U", (), {"user_id": "u1"})()  # duck-typed GatewayUser
    status = nas_authorization_service.get_user_authorization(user)
    assert status.available is False
    assert status.authorized is False
    assert status.folders == []
    assert "socket" in status.reason or "token" in status.reason


def test_get_user_authorization_trim_success(monkeypatch):
    """飞牛场景 + trim 正常：folders 返回非空"""
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: True)

    fake = trim_gateway.UserAccessibleFolders(
        paths=["/vol1/1000/bills", "/vol1/1000/bills/2024"],
    )

    def fake_get(uid, *, app_name):
        assert app_name == "财务统计"  # APP_NAME 常量，与 manifest 对齐
        assert uid == trim_gateway.uid_from_user_id("u1")
        return fake

    monkeypatch.setattr(trim_gateway, "get_user_accessible_folders", fake_get)
    patch_shared(monkeypatch, ["/vol1/1000/shared-bills"])
    user = type("U", (), {"user_id": "u1"})()
    status = nas_authorization_service.get_user_authorization(user)
    assert status.available is True
    assert status.authorized is True
    assert status.folders == ["/vol1/1000/bills", "/vol1/1000/bills/2024"]
    assert status.shared_folders == ["/vol1/1000/shared-bills"]
    assert status.shared_reason == ""
    assert status.reason == ""


def test_get_user_authorization_trim_unavailable(monkeypatch):
    """飞牛场景 + trim 不可用：available=False 带原因文案"""
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: True)

    def fake_get(uid, *, app_name):
        raise trim_gateway.TrimGatewayUnavailable("socket 超时")

    monkeypatch.setattr(trim_gateway, "get_user_accessible_folders", fake_get)
    user = type("U", (), {"user_id": "u1"})()
    status = nas_authorization_service.get_user_authorization(user)
    assert status.available is False
    assert status.authorized is False
    assert "不可用" in status.reason or "超时" in status.reason


def test_get_user_authorization_trim_rejected(monkeypatch):
    """飞牛场景 + trim 返回非 0：available=False 带拒因"""
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: True)

    def fake_get(uid, *, app_name):
        raise trim_gateway.TrimGatewayRejected(1, "仅管理员可进行此操作")

    monkeypatch.setattr(trim_gateway, "get_user_accessible_folders", fake_get)
    user = type("U", (), {"user_id": "u1"})()
    status = nas_authorization_service.get_user_authorization(user)
    assert status.available is False
    assert "code=1" in status.reason


def test_check_path_acl_locally_passes_through(monkeypatch):
    """本地开发：宽松策略，全部 True"""
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: False)
    user = type("U", (), {"user_id": "u1"})()
    out = nas_authorization_service.check_path_acl(user, ["a.csv", "sub/b.xlsx"])
    assert all(v["readable"] and v["writable"] and v["deletable"] for v in out.values())
    assert set(out.keys()) == {"a.csv", "sub/b.xlsx"}


def test_check_path_acl_trim_success(monkeypatch):
    """飞牛场景：trim 返回结构化权限"""
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: True)
    monkeypatch.setattr(
        nas_authorization_service,
        "load_nas_settings",
        lambda: type("S", (), {"import_dir": "/vol1/1000/bills"})(),
    )

    captured: dict = {}

    def fake_check(uid, paths, *, app_name):
        captured["uid"] = uid
        captured["paths"] = list(paths)
        return [
            trim_gateway.AclEntry(
                path="/vol1/1000/bills/a.csv", readable=True, writable=False, deletable=False
            ),
            trim_gateway.AclEntry(
                path="/vol1/1000/bills/sub/b.xlsx",
                readable=False,
                writable=False,
                deletable=False,
            ),
        ]

    monkeypatch.setattr(trim_gateway, "check_user_acl", fake_check)
    user = type("U", (), {"user_id": "u1"})()
    out = nas_authorization_service.check_path_acl(user, ["a.csv", "sub/b.xlsx"])
    assert out["a.csv"]["readable"] is True
    assert out["a.csv"]["writable"] is False
    assert out["sub/b.xlsx"]["readable"] is False
    # trim 调用的应是绝对路径
    assert all(p.startswith("/vol1/1000/bills") for p in captured["paths"])


def test_check_path_acl_no_root_skips_trim(monkeypatch):
    """账单目录未配置时不应发起 trim 调用（root 为空 → 直接返回空 dict）

    守住这条：若将来有人改成"root 为空也调 trim"，会把无意义的请求打进网关，
    且返回的绝对路径映射不到相对路径，属于纯浪费。
    """
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: True)
    monkeypatch.setattr(
        nas_authorization_service,
        "load_nas_settings",
        lambda: type("S", (), {"import_dir": ""})(),
    )
    called = {"n": 0}

    def spy(uid, paths, *, app_name):
        called["n"] += 1
        return []

    monkeypatch.setattr(trim_gateway, "check_user_acl", spy)
    user = type("U", (), {"user_id": "u1"})()
    out = nas_authorization_service.check_path_acl(user, ["a.csv"])
    assert called["n"] == 0, "root 为空时应跳过 trim 调用"
    assert out == {}


def test_check_path_acl_trim_unavailable_falls_open(monkeypatch):
    """飞牛 trim 不可用时降级为宽松 True，不阻塞主流程"""
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: True)
    monkeypatch.setattr(
        nas_authorization_service,
        "load_nas_settings",
        lambda: type("S", (), {"import_dir": "/vol1/1000/bills"})(),
    )

    def fake_check(uid, paths, *, app_name):
        raise trim_gateway.TrimGatewayUnavailable("token 失效")

    monkeypatch.setattr(trim_gateway, "check_user_acl", fake_check)
    user = type("U", (), {"user_id": "u1"})()
    out = nas_authorization_service.check_path_acl(user, ["a.csv"])
    assert out["a.csv"]["readable"] is True


# --------------------- 应用共享授权（v0.5.1） ---------------------


def test_get_shared_accessible_folders_parses_paths(monkeypatch):
    """共享目录响应解析：去空白、丢空串、去重保序"""
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: True)

    def fake_request(req, data, *, app_name, timeout_sec=0.0):
        assert req == "trim.file.getSharedAccessibleFolders"
        assert data == {}, "共享目录是应用级查询，不需要任何参数"
        return {"paths": ["/vol1/1000/bills", "   ", "/vol1/1000/bills"]}

    monkeypatch.setattr(trim_gateway, "_request", fake_request)
    out = trim_gateway.get_shared_accessible_folders(app_name="财务统计")
    assert out.paths == ["/vol1/1000/bills"]


def test_shared_folder_failure_does_not_downgrade_available(monkeypatch):
    """共享目录查询失败只影响 shared_* 字段，整体仍 available=True

    守住这条很重要：共享目录是"可选加成"，它失败（管理员没配 / scope 没生效）
    不该把整个授权区打灰，否则一次抖动就会让个人授权也跟着消失。
    """
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: True)
    monkeypatch.setattr(
        trim_gateway,
        "get_user_accessible_folders",
        lambda uid, *, app_name: trim_gateway.UserAccessibleFolders(
            paths=["/vol1/1000/bills"]
        ),
    )
    patch_shared(monkeypatch, exc=trim_gateway.TrimGatewayRejected(1, "scope missing"))
    user = type("U", (), {"user_id": "u1"})()
    status = nas_authorization_service.get_user_authorization(user)
    assert status.available is True, "共享目录失败不应把整个授权区打成不可用"
    assert status.authorized is True
    assert status.shared_folders == []
    assert "scope missing" in status.shared_reason


def test_unauthorized_reason_points_to_shared_when_present(monkeypatch):
    """用户没授权个人目录、但有共享目录时，提示应引导去用共享目录"""
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: True)
    monkeypatch.setattr(
        trim_gateway,
        "get_user_accessible_folders",
        lambda uid, *, app_name: trim_gateway.UserAccessibleFolders(paths=[]),
    )
    patch_shared(monkeypatch, ["/vol1/1000/shared"])
    user = type("U", (), {"user_id": "u1"})()
    status = nas_authorization_service.get_user_authorization(user)
    assert status.authorized is False
    assert "共享目录" in status.reason


def test_resolve_effective_import_dir_order(monkeypatch):
    """优先级：旧配置 > 共享目录 > 个人目录

    旧配置必须最优先（用户显式保存过，不能被新能力顶掉）；
    共享目录排在个人目录前，因为它是管理员一次性配好的应用级默认值。
    """
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: True)
    monkeypatch.setattr(
        trim_gateway,
        "get_user_accessible_folders",
        lambda uid, *, app_name: trim_gateway.UserAccessibleFolders(
            paths=["/vol1/1000/personal"]
        ),
    )
    patch_shared(monkeypatch, ["/vol1/1000/shared"])
    user = type("U", (), {"user_id": "u1"})()

    def set_legacy(value):
        monkeypatch.setattr(
            nas_authorization_service,
            "load_nas_settings",
            lambda: type("S", (), {"import_dir": value})(),
        )

    set_legacy("/vol1/1000/legacy")
    assert (
        nas_authorization_service.resolve_effective_import_dir(user)[0]
        == "/vol1/1000/legacy"
    )

    set_legacy("")
    assert (
        nas_authorization_service.resolve_effective_import_dir(user)[0]
        == "/vol1/1000/shared"
    )

    patch_shared(monkeypatch, [])
    assert (
        nas_authorization_service.resolve_effective_import_dir(user)[0]
        == "/vol1/1000/personal"
    )


def test_is_admin_passthrough_tolerates_missing_attr(monkeypatch):
    """is_admin 透传给前端做按钮显隐；鸭子类型对象缺该属性时不炸"""
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: False)
    admin = type("U", (), {"user_id": "u1", "is_admin": True})()
    assert nas_authorization_service.get_user_authorization(admin).is_admin is True
    plain = type("U", (), {"user_id": "u1"})()
    assert nas_authorization_service.get_user_authorization(plain).is_admin is False


# --------------------------- HTTP 路由测试 ---------------------------


def test_route_authorization_returns_status(client, monkeypatch):
    """GET /api/nas/authorization 正常 200 + 体格式"""
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: False)
    r = client.get("/api/nas/authorization")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["available"] is False
    assert "reason" in data and data["reason"]


def test_route_check_acl_returns_map(client, monkeypatch):
    """POST /api/nas/authorization/check-acl 正常 200 + path->perms 结构"""
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: False)
    r = client.post(
        "/api/nas/authorization/check-acl", json={"paths": ["a.csv", "b.xlsx"]}
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert set(data.keys()) == {"a.csv", "b.xlsx"}
    assert data["a.csv"]["readable"] is True


def test_route_check_acl_empty_paths_is_empty(client):
    """空路径列表：返回空 dict，不报错"""
    r = client.post("/api/nas/authorization/check-acl", json={"paths": []})
    assert r.status_code == 200
    assert r.json()["data"] == {}


def test_route_check_acl_rejects_too_many(client):
    """超长列表被 Pydantic 拦下（防 DoS）"""
    too_many = [f"p{i}" for i in range(201)]
    r = client.post("/api/nas/authorization/check-acl", json={"paths": too_many})
    assert r.status_code == 422  # Pydantic validation


def test_route_authorization_trim_failure_still_200(client, monkeypatch):
    """trim 网关报错时路由仍 200，不抛 5xx"""
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: True)

    def fake_get(uid, *, app_name):
        raise trim_gateway.TrimGatewayRejected(403, "scope missing")

    monkeypatch.setattr(trim_gateway, "get_user_accessible_folders", fake_get)
    r = client.get("/api/nas/authorization")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["available"] is False
    assert data["authorized"] is False
    assert "403" in data["reason"] or "scope" in data["reason"]


def test_route_authorization_exposes_shared_and_admin(client, monkeypatch):
    """路由要透出 shared_folders / is_admin，供前端决定按钮显隐"""
    monkeypatch.setattr(trim_gateway, "is_trim_runtime", lambda: True)
    monkeypatch.setattr(
        trim_gateway,
        "get_user_accessible_folders",
        lambda uid, *, app_name: trim_gateway.UserAccessibleFolders(
            paths=["/vol1/1000/bills"]
        ),
    )
    patch_shared(monkeypatch, ["/vol1/1000/shared"])
    r = client.get("/api/nas/authorization", headers={"X-Trim-Isadmin": "true"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["shared_folders"] == ["/vol1/1000/shared"]
    assert data["is_admin"] is True
    assert data["available"] is True


def test_existing_endpoints_unchanged(client):
    """旧的 config / list 端点行为不应受新模块影响"""
    r = client.get("/api/nas/config")
    assert r.status_code == 200
    assert r.json()["code"] == 0
