"""操作审计测试（T-7.6）：diff 摘要、打点冒烟、权限裁剪（管理员全部 / 普通仅自己）"""

from app.services import audit_service
from tests.conftest import USER_A, USER_B

A_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Username": "zhangsan"}
B_HEADERS = {"X-Trim-Userid": USER_B, "X-Trim-Username": "lisi"}
ADMIN_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Isadmin": "true"}


def test_diff_summary_update():
    before = {"amount": 10, "category": "餐饮", "merchant": "同"}
    after = {"amount": 20, "category": "交通", "merchant": "同"}
    diff = audit_service.diff_summary(
        before, after, {"amount": "金额", "category": "分类", "merchant": "商户"}
    )
    assert diff == "金额 10→20；分类 餐饮→交通"  # 未变化的字段不出现


def test_diff_summary_create_and_delete():
    fields = {"merchant": "商户", "amount": "金额"}
    created = audit_service.diff_summary(None, {"merchant": "X", "amount": 5}, fields)
    assert created == "新增：商户 X；金额 5"
    deleted = audit_service.diff_summary({"merchant": "X", "amount": 5}, None, fields)
    assert deleted == "删除：商户 X；金额 5"
    # 空值字段不进摘要
    empty = audit_service.diff_summary(None, {"merchant": "", "amount": None}, fields)
    assert empty == "新增"


def test_record_is_best_effort(client, db, monkeypatch):
    """审计写入失败只记 warning，不影响业务主流程"""
    from app.db.dao import audit_dao

    def boom(*a, **k):
        raise RuntimeError("audit down")

    monkeypatch.setattr(audit_dao.AuditDAO, "create", boom)
    # 不抛异常即为主流程不受影响的契约
    audit_service.record(USER_A, "bill.create", "bill", 1, "x")


def test_audit_scoped_by_account_and_admin_filter(client, db):
    """普通账号只能看自己的操作；管理员可看全部并按操作人过滤"""
    # A、B 各产生一条写操作
    a_res = client.post(
        "/api/loans",
        headers=A_HEADERS,
        json={
            "direction": "lend",
            "counterparty": "甲",
            "principal": 100,
            "loan_date": "2026-09-01",
        },
    )
    assert a_res.status_code == 201
    b_res = client.post(
        "/api/loans",
        headers=B_HEADERS,
        json={
            "direction": "borrow",
            "counterparty": "乙",
            "principal": 50,
            "loan_date": "2026-09-02",
        },
    )
    assert b_res.status_code == 201

    # 普通账号：仅自己的操作
    mine = client.get("/api/audit", headers=A_HEADERS).json()["data"]
    assert mine["total"] >= 1
    assert all(i["user_id"] == USER_A for i in mine["items"])
    assert all(i["action"] == "loan.create" for i in mine["items"])

    # 管理员：全部账号可见，并可按操作人过滤
    all_logs = client.get("/api/audit", headers=ADMIN_HEADERS).json()["data"]
    users = {i["user_id"] for i in all_logs["items"]}
    assert {USER_A, USER_B} <= users
    only_b = client.get(
        "/api/audit", params={"user_id": USER_B}, headers=ADMIN_HEADERS
    ).json()["data"]
    assert only_b["total"] >= 1
    assert all(i["user_id"] == USER_B for i in only_b["items"])


def test_audit_action_filter_and_summary(client, db):
    """打点带差异摘要：编辑流水后审计里能看到前后变化"""
    bill = client.post(
        "/api/bill",
        json={
            "tx_time": "2026-09-05 10:00:00",
            "account": "wechat",
            "tx_type": "expense",
            "amount": 10,
            "merchant": "审计",
            "category": "餐饮",
        },
        headers=A_HEADERS,
    ).json()["data"]
    client.put(
        f"/api/bill/{bill['id']}",
        json={"amount": 99, "category": "交通"},
        headers=A_HEADERS,
    )
    logs = client.get(
        "/api/audit", params={"action": "bill.update"}, headers=A_HEADERS
    ).json()["data"]
    assert logs["total"] >= 1
    summary = logs["items"][0]["summary"]
    # 金额经 normalize_amount 存为浮点：摘要里是 10.0→99.0
    assert "金额 10.0→99.0" in summary
    assert "分类 餐饮→交通" in summary
    assert "商户" not in summary  # 未变化字段不出现
