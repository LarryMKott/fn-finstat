"""通知中心测试（T-5.4）：事件去重、逐类开关、按账号可见性与已读水位线、
出站 Webhook（全 mock，不联网）、事件生产者挂接与接口权限
"""

import urllib.error

import pytest

from app.config import NotifySettings, load_notify_settings, save_notify_settings
from app.db.dao import notify_dao, task_dao
from app.db.dao.notify_dao import NotificationDAO
from app.services import notify_service, scheduler
from tests.conftest import USER_A, USER_B, make_bill_records

KEY = "nas_watch"


# ---- DAO：写入去重 / 可见性 / 已读水位线 ----


def test_create_dedup_by_event_key(db):
    """同一 event_key 只落库一次（唯一约束兜底），不同 key 互不影响"""
    first = NotificationDAO.create(
        user_id="",
        event_type="import_done",
        event_key="import_done:1",
        title="t",
        content="c",
    )
    assert first is not None
    duplicate = NotificationDAO.create(
        user_id="",
        event_type="import_done",
        event_key="import_done:1",
        title="t",
        content="c",
    )
    assert duplicate is None
    other = NotificationDAO.create(
        user_id="",
        event_type="import_done",
        event_key="import_done:2",
        title="t",
        content="c",
    )
    assert other is not None


def test_list_visibility_broadcast_and_targeted(db):
    """广播通知所有人可见；定向通知仅目标账号可见"""
    NotificationDAO.create(
        user_id="", event_type="task_failed", event_key="k1", title="广播", content=""
    )
    NotificationDAO.create(
        user_id=USER_A,
        event_type="report_ready",
        event_key="k2",
        title="私信",
        content="",
    )
    a_titles = [n["title"] for n in NotificationDAO.list_for_user(USER_A)]
    b_titles = [n["title"] for n in NotificationDAO.list_for_user(USER_B)]
    assert "广播" in a_titles and "私信" in a_titles
    assert "广播" in b_titles and "私信" not in b_titles


def test_unread_and_read_watermark_per_user(db):
    """未读数与「全部已读」按账号独立：A 已读不影响 B 的角标"""
    for i in range(3):
        NotificationDAO.create(
            user_id="",
            event_type="import_done",
            event_key=f"k{i}",
            title="t",
            content="",
        )
    assert NotificationDAO.unread_count(USER_A) == 3
    assert NotificationDAO.unread_count(USER_B) == 3

    NotificationDAO.mark_all_read(USER_A)
    assert NotificationDAO.unread_count(USER_A) == 0
    assert NotificationDAO.unread_count(USER_B) == 3

    # 新通知到达后再次计为未读
    NotificationDAO.create(
        user_id="", event_type="import_done", event_key="k-new", title="t", content=""
    )
    assert NotificationDAO.unread_count(USER_A) == 1
    # 本地无网关身份（user_id=""）等价一个独立账号，水位线互不干扰
    assert NotificationDAO.unread_count("") == 4
    NotificationDAO.mark_all_read("")
    assert NotificationDAO.unread_count("") == 0


def test_set_push_result(db):
    row = NotificationDAO.create(
        user_id="", event_type="task_failed", event_key="k", title="t", content=""
    )
    NotificationDAO.set_push_result(row["id"], False, "HTTP 500")
    stored = NotificationDAO.list_for_user("")[0]
    assert stored["push_status"] == "failed"
    assert stored["push_error"] == "HTTP 500"
    NotificationDAO.set_push_result(row["id"], True)
    stored = NotificationDAO.list_for_user("")[0]
    assert stored["push_status"] == "ok"
    assert stored["push_error"] == ""


# ---- 服务层：开关 / 配置视图 / 事件创建 ----


def test_event_enabled_defaults_and_switch(db):
    """未配置过的类型默认开启；配置关闭后不再产生该类通知"""
    assert notify_service.event_enabled("budget_exceeded") is True

    save_notify_settings(NotifySettings(events={"budget_exceeded": False}))
    assert load_notify_settings().resolved_events().get("budget_exceeded") is False
    assert notify_service.event_enabled("budget_exceeded") is False
    # 其他类型不受影响
    assert notify_service.event_enabled("import_done") is True


def test_create_event_respects_switch_and_dedup(db, monkeypatch):
    monkeypatch.setattr(notify_service, "send_webhook", lambda *a: (True, ""))
    assert (
        notify_service.create_event("import_done", "t", "c", dedup_key="x") is not None
    )
    # 重复事件静默跳过
    assert notify_service.create_event("import_done", "t", "c", dedup_key="x") is None

    save_notify_settings(NotifySettings(events={"import_done": False}))
    assert notify_service.create_event("import_done", "t", "c", dedup_key="y") is None


def test_create_event_survives_internal_failure(db, monkeypatch):
    """事件处理内部异常只吞掉（返回 None），不打断调用方"""

    def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(notify_dao.NotificationDAO, "create", boom)
    assert notify_service.create_event("import_done", "t", "c") is None


def test_config_view_masks_url_and_keep_semantics(db):
    """配置视图不回传 URL 明文；保存时 url=None 保持不变、空串清除"""
    save_notify_settings(
        NotifySettings(
            webhook_enabled=True,
            webhook_type="bark",
            webhook_url="https://api.day.app/abc12345",
        )
    )
    view = notify_service.get_config_view()
    assert view["webhook"]["has_url"] is True
    assert view["webhook"]["url_hint"] == "****c12345"
    assert "abc12345" not in str(view)

    # events 白名单：未知类型被丢弃
    notify_service.save_config_view({"budget_exceeded": False, "evil": True}, None)
    stored = load_notify_settings()
    assert stored.resolved_events() == {"budget_exceeded": False}

    # url=None（缺省）保持不变；url="" 清除
    notify_service.save_config_view(None, {"enabled": True, "type": "wecom"})
    kept = load_notify_settings()
    assert kept.webhook_url == "https://api.day.app/abc12345"
    notify_service.save_config_view(None, {"url": ""})
    cleared = load_notify_settings()
    assert cleared.webhook_url == ""
    # 清除 URL 后 enabled 自动失效（sanitized 收敛）
    assert cleared.webhook_enabled is False


# ---- 出站 Webhook（全 mock） ----


class _FakeResponse:
    def __init__(self, status=200, payload=b""):
        self.status = status
        self._payload = payload

    def read(self, n=-1):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeOpener:
    def __init__(self, response=None, error=None):
        self.requests = []
        self._response = response or _FakeResponse()
        self._error = error

    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self._error is not None:
            raise self._error
        return self._response


@pytest.fixture()
def fake_opener(monkeypatch):
    holder = {}

    def _install(response=None, error=None):
        opener = _FakeOpener(response=response, error=error)
        monkeypatch.setattr(
            notify_service.urllib.request, "build_opener", lambda *a, **k: opener
        )
        holder["opener"] = opener
        return opener

    return _install


def test_send_webhook_rejects_non_http_scheme(db):
    ok, error = notify_service.send_webhook("generic", "ftp://x", "t", "c")
    assert ok is False
    assert "http" in error


def test_send_webhook_generic_success(db, fake_opener):
    opener = fake_opener(response=_FakeResponse(status=200, payload=b"ok"))
    ok, error = notify_service.send_webhook(
        "generic", "https://hook.example/x", "标题", "内容"
    )
    assert ok is True and error == ""
    request, timeout = opener.requests[0]
    assert timeout == notify_service.WEBHOOK_TIMEOUT
    body = request.data.decode("utf-8")
    assert "标题" in body and "内容" in body


def test_send_webhook_wecom_errcode(db, fake_opener):
    """HTTP 200 但 errcode != 0 判定为失败（渠道业务码校验）"""
    fake_opener(
        response=_FakeResponse(payload=b'{"errcode":93000,"errmsg":"invalid webhook"}')
    )
    ok, error = notify_service.send_webhook(
        "wecom", "https://qyapi.weixin.qq.com/x", "t", "c"
    )
    assert ok is False
    assert "93000" in error

    fake_opener(response=_FakeResponse(payload=b'{"errcode":0}'))
    ok, _ = notify_service.send_webhook(
        "wecom", "https://qyapi.weixin.qq.com/x", "t", "c"
    )
    assert ok is True


def test_send_webhook_http_error_and_exception(db, fake_opener):
    fake_opener(error=urllib.error.HTTPError("u", 500, "boom", None, None))
    ok, error = notify_service.send_webhook(
        "generic", "https://hook.example/x", "t", "c"
    )
    assert ok is False and error == "HTTP 500"

    fake_opener(error=OSError("connection refused"))
    ok, error = notify_service.send_webhook(
        "generic", "https://hook.example/x", "t", "c"
    )
    assert ok is False and "OSError" in error


def test_send_webhook_bark_get_path_quoted(db, fake_opener):
    opener = fake_opener()
    notify_service.send_webhook("bark", "https://api.day.app/key/", "标 题", "内/容")
    request, _ = opener.requests[0]
    assert request.get_method() == "GET"
    # 路径段已 URL 编码，避免特殊字符破坏 URL 结构
    assert "%E6%A0%87" in request.full_url
    assert request.full_url.startswith("https://api.day.app/key/")


def test_push_webhook_records_failure(db, monkeypatch):
    """出站失败：通知保留且失败原因落库，不抛异常"""
    save_notify_settings(
        NotifySettings(
            webhook_enabled=True,
            webhook_type="generic",
            webhook_url="https://hook.example/x",
        )
    )
    monkeypatch.setattr(notify_service, "send_webhook", lambda *a: (False, "HTTP 503"))
    created = notify_service.create_event("import_done", "t", "c", dedup_key="p1")
    stored = NotificationDAO.list_for_user("")[0]
    assert created is not None
    assert stored["push_status"] == "failed"
    assert stored["push_error"] == "HTTP 503"

    # 未启用 Webhook 时不做出站尝试
    save_notify_settings(NotifySettings(webhook_enabled=False, webhook_url="https://x"))
    calls = []
    monkeypatch.setattr(
        notify_service, "send_webhook", lambda *a: calls.append(a) or (True, "")
    )
    notify_service.create_event("import_done", "t", "c", dedup_key="p2")
    assert calls == []


# ---- 事件生产者 ----


def test_notify_task_disabled(db, monkeypatch):
    monkeypatch.setattr(notify_service, "send_webhook", lambda *a: (True, ""))
    notify_service.notify_task_disabled(KEY, "NAS 目录监听导入", "RuntimeError: x")
    rows = NotificationDAO.list_for_user("")
    assert rows[0]["event_type"] == "task_failed"
    assert "NAS 目录监听导入" in rows[0]["title"]


def test_notify_report_ready_is_user_scoped(db):
    notify_service.notify_report_ready(USER_A, "2026-08 月度报告")
    a_contents = [n["content"] for n in NotificationDAO.list_for_user(USER_A)]
    b_contents = [n["content"] for n in NotificationDAO.list_for_user(USER_B)]
    assert any("月度报告" in c for c in a_contents)
    assert all("月度报告" not in c for c in b_contents)


def test_check_budget_events_exceeded_and_dedup(db):
    """超支产生事件；同月同分类去重，第二次检查不重复提醒"""
    from app.db.dao.budget_dao import BudgetDAO
    from app.db.dao.bill_dao import BillDAO

    BillDAO.insert_many(
        make_bill_records(
            100,
            prefix="BUD",
            amount=90.0,
            category="餐饮",
            tx_time="2026-08-05 10:00:00",
        ),
        USER_A,
    )
    BudgetDAO.upsert(USER_A, "2026-08", "餐饮", 100.0)

    notify_service.check_budget_events(USER_A, "2026-08")
    types = sorted(n["event_type"] for n in NotificationDAO.list_for_user(USER_A))
    assert types == ["budget_exceeded"]

    # 预算提高后支出落在 80%~100% 区间：跨过「接近上限」阈值，产生一次提醒
    # （去重键按 类型+月+分类 区分，不同类型的阈值跨越各自提醒一次）
    BudgetDAO.upsert(USER_A, "2026-08", "餐饮", 10000.0)
    notify_service.check_budget_events(USER_A, "2026-08")
    types = sorted(n["event_type"] for n in NotificationDAO.list_for_user(USER_A))
    assert types == ["budget_exceeded", "budget_near_limit"]
    # 再次检查：两类各自已提醒过，不再重复
    notify_service.check_budget_events(USER_A, "2026-08")
    types = sorted(n["event_type"] for n in NotificationDAO.list_for_user(USER_A))
    assert types == ["budget_exceeded", "budget_near_limit"]


def test_check_budget_events_near_limit(db):
    from app.db.dao.budget_dao import BudgetDAO
    from app.db.dao.bill_dao import BillDAO

    BillDAO.insert_many(
        make_bill_records(
            8,
            prefix="NEAR",
            amount=10.0,
            category="交通",
            tx_time="2026-08-05 10:00:00",
        ),
        USER_B,
    )
    BudgetDAO.upsert(USER_B, "2026-08", "交通", 100.0)
    notify_service.check_budget_events(USER_B, "2026-08")
    rows = NotificationDAO.list_for_user(USER_B)
    assert rows[0]["event_type"] == "budget_near_limit"
    assert "80%" in rows[0]["content"]


# ---- 集成：调度停用 / 目录导入完成 ----


@pytest.fixture(autouse=True)
def _restore_registry():
    """快照/还原调度注册表：覆盖的 fn 不泄漏到其他测试"""
    snapshot = {k: dict(v) for k, v in scheduler._REGISTRY.items()}
    yield
    scheduler._REGISTRY.clear()
    scheduler._REGISTRY.update(snapshot)


def test_task_auto_disable_creates_notification(db, monkeypatch):
    """连续失败达到上限自动停用时产生通知事件（REQ-AUT-004 收口）"""
    monkeypatch.setattr(notify_service, "send_webhook", lambda *a: (True, ""))

    def boom():
        raise RuntimeError("持续失败")

    scheduler.register_task(KEY, "目录监听导入", 30, boom)
    task_dao.TaskDAO.ensure_task(KEY, "目录监听导入", 30)
    for _ in range(task_dao.MAX_FAILURES):
        scheduler.run_task_now(KEY)

    task = task_dao.TaskDAO.get(KEY)
    assert task["enabled"] is False
    rows = NotificationDAO.list_for_user("")
    assert any(
        n["event_type"] == "task_failed" and "目录监听导入" in n["title"] for n in rows
    )


def test_import_watch_done_creates_notification(db, tmp_path, monkeypatch):
    """目录监听有新增流水时产生「导入完成」通知并联动预算检查"""
    from pathlib import Path

    from app.services import nas_service
    from tests.test_nas import ALIPAY_ROWS, write_csv

    monkeypatch.setattr(notify_service, "send_webhook", lambda *a: (True, ""))
    watch_dir = tmp_path / "bills"
    watch_dir.mkdir()
    nas_service.update_config(
        type("P", (), {"import_dir": str(watch_dir)}), owner_user_id=USER_A
    )
    write_csv(Path(watch_dir) / "alipay.csv", ALIPAY_ROWS)

    from app.services import import_watch_service

    affected, _ = import_watch_service.scan_and_import()
    assert affected == 1
    rows = [
        n
        for n in NotificationDAO.list_for_user(USER_A)
        if n["event_type"] == "import_done"
    ]
    assert len(rows) == 1
    assert "新增流水 1 条" in rows[0]["content"]

    # 无新增的第二轮不再通知
    import_watch_service.scan_and_import()
    rows = [
        n
        for n in NotificationDAO.list_for_user(USER_A)
        if n["event_type"] == "import_done"
    ]
    assert len(rows) == 1


# ---- 接口：列表 / 角标 / 已读 / 配置 / 权限 ----

ADMIN_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Isadmin": "true"}
B_HEADERS = {"X-Trim-Userid": USER_B}


def test_api_notification_flow(client, db, monkeypatch):
    """列表 → 角标 → 全部已读 的完整链路，跨账号已读互不影响"""
    monkeypatch.setattr(notify_service, "send_webhook", lambda *a: (True, ""))
    notify_service.create_event("import_done", "标题", "内容", dedup_key="api1")
    notify_service.create_event(
        "report_ready", "报告", "好了", user_id=USER_A, dedup_key="api2"
    )

    resp = client.get("/api/notifications", headers={"X-Trim-Userid": USER_A})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["unread"] == 2
    assert [i["read"] for i in data["items"]] == [False, False]

    resp = client.get("/api/notifications/unread-count", headers=B_HEADERS)
    assert resp.json()["data"]["unread"] == 1  # B 只看得到广播

    resp = client.post("/api/notifications/read-all", headers={"X-Trim-Userid": USER_A})
    assert resp.status_code == 200
    assert resp.json()["data"]["last_read_id"] > 0

    resp = client.get("/api/notifications", headers={"X-Trim-Userid": USER_A})
    assert resp.json()["data"]["unread"] == 0
    assert all(i["read"] for i in resp.json()["data"]["items"])
    # B 不受影响
    assert (
        client.get("/api/notifications/unread-count", headers=B_HEADERS).json()["data"][
            "unread"
        ]
        == 1
    )


def test_api_notify_config_permissions(client, db):
    """配置读对所有登录用户开放；保存与测试推送仅管理员（双层防御）"""
    resp = client.get("/api/settings/notify/config", headers=B_HEADERS)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["events"]) == len(notify_service.EVENT_TYPES)
    assert data["webhook"]["enabled"] is False

    assert (
        client.put(
            "/api/settings/notify/config",
            json={"events": {"import_done": False}},
            headers=B_HEADERS,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/settings/notify/webhook-test",
            json={"type": "generic", "url": "https://x.example"},
            headers=B_HEADERS,
        ).status_code
        == 403
    )

    resp = client.put(
        "/api/settings/notify/config",
        json={
            "events": {"import_done": False},
            "webhook": {"enabled": True, "type": "bark"},
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    by_type = {e["type"]: e["enabled"] for e in data["events"]}
    assert by_type["import_done"] is False
    assert by_type["budget_exceeded"] is True  # 未提及的类型保持默认开
    assert data["webhook"]["type"] == "bark"


def test_api_webhook_test_invalid_url(client, db):
    resp = client.post(
        "/api/settings/notify/webhook-test",
        json={"type": "generic", "url": "not-a-url"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is False
    assert "http" in data["message"]


def test_api_webhook_test_falls_back_to_saved_url(client, db, monkeypatch):
    """url 缺省时回退用已保存配置测试（界面只回显掩码，前端拿不到原值）"""
    from app.config import NotifySettings, save_notify_settings

    save_notify_settings(
        NotifySettings(
            webhook_enabled=True,
            webhook_type="generic",
            webhook_url="https://saved.example/hook",
        )
    )
    captured = {}
    monkeypatch.setattr(
        notify_service,
        "test_webhook",
        lambda wtype, url: captured.update(type=wtype, url=url) or (True, "发送成功"),
    )
    resp = client.post(
        "/api/settings/notify/webhook-test", json={"url": ""}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["ok"] is True
    assert captured == {"type": "generic", "url": "https://saved.example/hook"}

    # 无任何已保存地址时给可读错误
    save_notify_settings(NotifySettings())
    resp = client.post(
        "/api/settings/notify/webhook-test", json={"url": ""}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 400


def test_admin_surface_rules_cover_notify():
    """权限中间件策略表覆盖通知配置写操作（第一层防御）"""
    from app.core.permissions import is_admin_surface

    assert is_admin_surface("/api/settings/notify/config", "PUT") is True
    assert is_admin_surface("/api/settings/notify/webhook-test", "POST") is True
    assert is_admin_surface("/api/settings/notify/config", "GET") is False
    assert is_admin_surface("/api/notifications", "GET") is False
    assert is_admin_surface("/api/notifications/read-all", "POST") is False
