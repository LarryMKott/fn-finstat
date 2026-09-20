"""家庭空间测试（T-7.2）：创建/加入/退出/解散、聚合汇总、隐私边界

覆盖计划 REQ-FAM-002/003 的两条行为底线：
- 两账号数据明细互不可见（默认关闭互看，家庭页只有聚合值）
- 家庭页汇总数 = 各成员实际之和（逐成员给出收支与条数，逐项核对）
"""

import pytest

from app.core.errors import NotFoundError, ValidationError
from app.db.dao.bill_dao import BillDAO
from app.db.dao.family_dao import FamilyDAO
from app.services import family_service
from tests.conftest import USER_A, USER_B, make_bill_records

A_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Username": "zhangsan"}
B_HEADERS = {"X-Trim-Userid": USER_B, "X-Trim-Username": "lisi"}


@pytest.fixture()
def family(client, db):
    """A 建家庭、B 凭码加入；返回邀请码"""
    created = client.post("/api/family", json={"name": "我们家"}, headers=A_HEADERS)
    assert created.status_code == 201
    code = created.json()["data"]["invite_code"]
    joined = client.post("/api/family/join", json={"code": code}, headers=B_HEADERS)
    assert joined.status_code == 200
    return {"code": code, "family_id": created.json()["data"]["id"]}


def test_create_family_creator_is_admin(client, db):
    res = client.post("/api/family", json={"name": "我们家"}, headers=A_HEADERS)
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["name"] == "我们家"
    assert data["my_role"] == "admin"
    assert len(data["invite_code"]) == 8
    assert [m["role"] for m in data["members"]] == ["admin"]
    # 成员昵称取自网关用户名快照
    assert data["members"][0]["nickname"] == "zhangsan"


def test_no_family_returns_null(client, db):
    res = client.get("/api/family", headers=A_HEADERS)
    assert res.status_code == 200 and res.json()["data"] is None
    with pytest.raises(NotFoundError):
        family_service.summary(USER_A, "2026-08")


def test_join_by_invite_code(client, db):
    created = client.post("/api/family", json={"name": "我们家"}, headers=A_HEADERS)
    code = created.json()["data"]["invite_code"]

    joined = client.post("/api/family/join", json={"code": code}, headers=B_HEADERS)
    assert joined.status_code == 200
    assert joined.json()["data"]["family_name"] == "我们家"

    # 成员视角：my_role=member，看不到邀请码
    info = client.get("/api/family", headers=B_HEADERS).json()["data"]
    assert info["my_role"] == "member" and info["invite_code"] is None
    assert {m["user_id"] for m in info["members"]} == {USER_A, USER_B}


def test_join_invalid_code_and_duplicates(client, db):
    assert (
        client.post(
            "/api/family/join", json={"code": "WRONGCODE"}, headers=B_HEADERS
        ).status_code
        == 404
    )
    client.post("/api/family", json={"name": "A家"}, headers=A_HEADERS)
    # 已在家庭：不能重复创建，也不能再加入（ConflictError 按契约返回 400）
    assert (
        client.post("/api/family", json={"name": "A家2"}, headers=A_HEADERS).status_code
        == 400
    )
    code = client.get("/api/family", headers=A_HEADERS).json()["data"]["invite_code"]
    assert (
        client.post(
            "/api/family/join", json={"code": code}, headers=A_HEADERS
        ).status_code
        == 400
    )


def test_summary_equals_member_sum(client, db, family):
    """验收底线：家庭汇总 = 各成员实际之和（A 出 3 笔 ×10，B 出 2 笔 ×25）"""
    BillDAO.insert_many(
        make_bill_records(3, prefix="FA", tx_time="2026-08-05 10:00:00", amount=10.0),
        USER_A,
    )
    BillDAO.insert_many(
        make_bill_records(2, prefix="FB", tx_time="2026-08-09 10:00:00", amount=25.0),
        USER_B,
    )

    res = client.get("/api/family/summary?month=2026-08", headers=B_HEADERS)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["totals"] == {"income": 0.0, "expense": 80.0, "net": -80.0}
    by_user = {m["user_id"]: m for m in data["members"]}
    assert by_user[USER_A]["bill_count"] == 3 and by_user[USER_A]["expense"] == 30.0
    assert by_user[USER_B]["bill_count"] == 2 and by_user[USER_B]["expense"] == 50.0
    # 成员视角同样可看汇总（REQ-FAM-003，不限于管理员）
    other = client.get("/api/family/summary?month=2026-08", headers=A_HEADERS)
    assert other.json()["data"]["totals"]["expense"] == 80.0
    # 空月汇总为 0，不报错
    empty = client.get("/api/family/summary?month=2026-09", headers=A_HEADERS)
    assert empty.json()["data"]["totals"]["expense"] == 0.0


def test_privacy_detail_view_default_closed(client, db, family):
    """默认关闭互看：成员请求他人流水一律 403（含管理员）"""
    BillDAO.insert_many(make_bill_records(2, prefix="PA"), USER_A)
    url = f"/api/family/members/{USER_A}/bills"
    assert client.get(url, headers=B_HEADERS).status_code == 403
    assert client.get(url, headers=A_HEADERS).status_code == 403


def test_privacy_detail_view_opt_in(client, db, family):
    """管理员开启 allow_detail_view 后，成员可互看流水（只读、仅本家庭）"""
    BillDAO.insert_many(make_bill_records(2, prefix="PA"), USER_A)
    BillDAO.insert_many(make_bill_records(1, prefix="PB"), USER_B)

    switched = client.put(
        "/api/family/settings",
        json={"allow_detail_view": True},
        headers=A_HEADERS,
    )
    assert switched.status_code == 200 and switched.json()["data"] == {
        "allow_detail_view": True
    }

    rows = client.get(f"/api/family/members/{USER_A}/bills", headers=B_HEADERS).json()[
        "data"
    ]
    # BillOut 不含 user_id，用 A 侧专用 tx_id 前缀判定归属
    assert rows["total"] == 2
    assert all(r["tx_id"].startswith("PA-") for r in rows["items"])


def test_settings_requires_family_admin(client, db, family):
    res = client.put(
        "/api/family/settings", json={"allow_detail_view": True}, headers=B_HEADERS
    )
    assert res.status_code == 403


def test_remove_member_and_rejoin(client, db, family):
    removed = client.delete(f"/api/family/members/{USER_B}", headers=A_HEADERS)
    assert removed.status_code == 204
    assert client.get("/api/family", headers=B_HEADERS).json()["data"] is None

    # 移除后可凭码重新加入；管理员不能移除自己；非管理员不能移除他人
    assert (
        client.post(
            "/api/family/join", json={"code": family["code"]}, headers=B_HEADERS
        ).status_code
        == 200
    )
    assert (
        client.delete(f"/api/family/members/{USER_A}", headers=A_HEADERS).status_code
        == 400
    )
    assert (
        client.delete(f"/api/family/members/{USER_A}", headers=B_HEADERS).status_code
        == 403
    )


def test_leave_and_admin_leave_guard(client, db, family):
    # 管理员在还有成员时不能退出
    assert client.post("/api/family/leave", headers=A_HEADERS).status_code == 400
    # 普通成员可退出
    assert client.post("/api/family/leave", headers=B_HEADERS).status_code == 204
    assert client.get("/api/family", headers=B_HEADERS).json()["data"] is None
    # 管理员成为最后一人后退出：家庭自动解散
    assert client.post("/api/family/leave", headers=A_HEADERS).status_code == 204
    assert FamilyDAO.member_of(USER_A) is None
    assert FamilyDAO.get(family["family_id"]) is None


def test_disband_family(client, db, family):
    # 非管理员不能解散
    assert client.delete("/api/family", headers=B_HEADERS).status_code == 403
    assert client.delete("/api/family", headers=A_HEADERS).status_code == 204
    assert client.get("/api/family", headers=A_HEADERS).json()["data"] is None
    assert client.get("/api/family", headers=B_HEADERS).json()["data"] is None
    # 解散后家庭表无残留
    assert FamilyDAO.get(family["family_id"]) is None


def test_regenerate_invite_code_invalidates_old(client, db, family):
    old = family["code"]
    assert (
        client.post("/api/family/invite/regenerate", headers=B_HEADERS).status_code
        == 403
    )
    res = client.post("/api/family/invite/regenerate", headers=A_HEADERS)
    assert res.status_code == 200
    new = res.json()["data"]["invite_code"]
    assert new != old
    # B 被移除后：旧码失效（404）、新码可用
    assert (
        client.delete(f"/api/family/members/{USER_B}", headers=A_HEADERS).status_code
        == 204
    )
    assert (
        client.post(
            "/api/family/join", json={"code": old}, headers=B_HEADERS
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/family/join", json={"code": new}, headers=B_HEADERS
        ).status_code
        == 200
    )


def test_service_validates_name_and_month(db):
    with pytest.raises(ValidationError):
        family_service.create_family("   ", USER_A)
    FamilyDAO.create("校验家庭", USER_A)
    with pytest.raises(ValidationError):
        family_service.summary(USER_A, "2026/08")


def test_leave_last_member_auto_disbands_with_budget(client, db):
    """最后一人退出即自动解散，家庭预算同事务清除（此前只测显式解散路径）"""
    created = client.post(
        "/api/family", json={"name": "退出解散之家"}, headers=A_HEADERS
    )
    assert created.status_code == 201
    client.put(
        "/api/budget/family",
        json={"month": "2026-09", "category": "餐饮", "amount": 200},
        headers=A_HEADERS,
    )
    assert client.post("/api/family/leave", headers=A_HEADERS).status_code == 204
    assert client.get("/api/family", headers=A_HEADERS).json()["data"] is None


def test_orphan_member_row_degrades_to_404_not_500(client, db):
    """孤儿成员行（成员行指向已解散家庭）：读接口 404 兜底而非 500，且可自救

    历史 bug：join 与解散竞态产生的孤儿行让 summary / member_bills 稳定
    500，该账号 create/join 又被 member_of 唯一命中挡成 409，界面显示
    「未加入家庭」却永远无法自救。
    """
    from app.db.engine import _STATE
    from app.db.models import FamilyMember

    # 手工制造孤儿成员行：成员行在、家庭行不在（家庭解散遗留）
    session = _STATE.new_session()
    session.add(
        FamilyMember(family_id=9999, user_id=USER_B, role="member", joined_at=0.0)
    )
    session.commit()

    assert client.get("/api/family", headers=B_HEADERS).json()["data"] is None
    assert (
        client.get("/api/family/summary?month=2026-09", headers=B_HEADERS).status_code
        == 404
    )
    assert (
        client.get("/api/family/members/USER_B/bills", headers=B_HEADERS).status_code
        == 404
    )
    assert (
        client.get("/api/family/budgets?month=2026-09", headers=B_HEADERS).status_code
        == 404
    )

    # 自救路径：退出孤儿行后可正常创建家庭
    assert client.post("/api/family/leave", headers=B_HEADERS).status_code == 204
    assert (
        client.post(
            "/api/family", json={"name": "重生之家"}, headers=B_HEADERS
        ).status_code
        == 201
    )
