"""家庭预算测试（T-7.3 共享预算）：管理员设定、全员进度、权限与生命周期

行为底线：
- 家庭预算金额由家庭管理员设定，普通成员只读进度（403 收口）
- 进度 = 全体成员当月实际支出之和（各账本合并，逐项可核对）
- 家庭预算与创建者本人的个人预算互不干扰（同月同分类可共存）
- 家庭解散时家庭预算随之清除，成员各自数据不受影响
"""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import DBSettings
from app.core.constants import family_scope_user
from app.core.errors import NotFoundError
from app.db.base import set_schema_version
from app.db.dao.bill_dao import BillDAO
from app.db.dao.budget_dao import BudgetDAO
from app.db.engine import _STATE
from app.db.models import Base
from app.schemas.budget import BudgetUpsert
from app.services import backup_service, budget_service
from tests.conftest import USER_A, USER_B, make_bill_records, make_engine

A_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Username": "zhangsan"}
B_HEADERS = {"X-Trim-Userid": USER_B, "X-Trim-Username": "lisi"}
MONTH = "2026-09"


@pytest.fixture()
def family(client, db):
    """A 建家庭、B 凭码加入"""
    created = client.post("/api/family", json={"name": "我们家"}, headers=A_HEADERS)
    assert created.status_code == 201
    code = created.json()["data"]["invite_code"]
    joined = client.post("/api/family/join", json={"code": code}, headers=B_HEADERS)
    assert joined.status_code == 200
    return {
        "family_id": created.json()["data"]["id"],
        "invite_code": code,
    }


def _seed_member_bills():
    """A 九月餐饮 60 + 交通 30，B 九月餐饮 40（同账本默认，家庭口径 = 之和）"""
    records = make_bill_records(
        1, prefix="FA", tx_time="2026-09-05 10:00:00", amount=60, category="餐饮"
    ) + make_bill_records(
        1, prefix="FB", tx_time="2026-09-06 10:00:00", amount=30, category="交通"
    )
    BillDAO.insert_many(records, USER_A)
    BillDAO.insert_many(
        make_bill_records(
            1, prefix="FC", tx_time="2026-09-07 10:00:00", amount=40, category="餐饮"
        ),
        USER_B,
    )


def test_family_budget_upsert_and_overview(client, db, family):
    _seed_member_bills()
    res = client.put(
        "/api/family/budgets",
        json={"month": MONTH, "category": "餐饮", "amount": 150},
        headers=A_HEADERS,
    )
    assert res.status_code == 200
    data = res.json()["data"]
    items = {i["category"]: i for i in data["items"]}
    assert items["餐饮"]["budget"] == 150
    assert items["餐饮"]["expense"] == 100  # A 60 + B 40，成员之和
    assert items["餐饮"]["remaining"] == 50

    # 成员只读同一份进度
    member_view = client.get(
        f"/api/family/budgets?month={MONTH}", headers=B_HEADERS
    ).json()["data"]
    assert member_view == data


def test_family_budget_total_and_category_mix(client, db, family):
    _seed_member_bills()
    client.put(
        "/api/family/budgets", json={"month": MONTH, "amount": 200}, headers=A_HEADERS
    )
    client.put(
        "/api/family/budgets",
        json={"month": MONTH, "category": "餐饮", "amount": 150},
        headers=A_HEADERS,
    )
    data = client.get(f"/api/family/budgets?month={MONTH}", headers=A_HEADERS).json()[
        "data"
    ]
    # 有总预算行时 total_budget 取总预算行，total_expense 为成员全部支出
    assert data["total_budget"] == 200
    assert data["total_expense"] == 130  # 60 + 30 + 40
    by_cat = {i["category"]: i for i in data["items"]}
    assert by_cat[""]["expense"] == 130
    assert by_cat["餐饮"]["expense"] == 100


def test_family_budget_admin_only(client, db, family):
    assert (
        client.put(
            "/api/family/budgets",
            json={"month": MONTH, "amount": 100},
            headers=B_HEADERS,
        ).status_code
        == 403
    )
    assert client.delete("/api/family/budgets/1", headers=B_HEADERS).status_code == 403


def test_family_budget_rejects_ledger_dimension(client, db, family):
    res = client.put(
        "/api/family/budgets",
        json={"month": MONTH, "amount": 100, "ledger_id": 1},
        headers=A_HEADERS,
    )
    assert res.status_code == 400


def test_family_budget_independent_from_personal(client, db, family):
    """创建者的个人预算与家庭预算同月同分类共存（合成属主避开唯一键）"""
    client.put(
        "/api/budget",
        json={"month": MONTH, "category": "餐饮", "amount": 100},
        headers=A_HEADERS,
    )
    client.put(
        "/api/family/budgets",
        json={"month": MONTH, "category": "餐饮", "amount": 300},
        headers=A_HEADERS,
    )
    personal = client.get(f"/api/budget?month={MONTH}", headers=A_HEADERS).json()[
        "data"
    ]
    family_view = client.get(
        f"/api/family/budgets?month={MONTH}", headers=A_HEADERS
    ).json()["data"]
    assert [i["budget"] for i in personal["items"]] == [100]
    assert [i["budget"] for i in family_view["items"]] == [300]


def test_family_budget_requires_membership(client, db):
    res = client.get(f"/api/family/budgets?month={MONTH}", headers=A_HEADERS)
    assert res.status_code == 404
    assert "家庭" in res.json()["msg"]


def test_delete_family_budget_scoped(client, db, family):
    created = client.put(
        "/api/family/budgets",
        json={"month": MONTH, "category": "餐饮", "amount": 150},
        headers=A_HEADERS,
    )
    budget_id = created.json()["data"]["items"][0]["id"]
    # 成员不可删
    assert (
        client.delete(f"/api/family/budgets/{budget_id}", headers=B_HEADERS).status_code
        == 403
    )
    assert (
        client.delete(f"/api/family/budgets/{budget_id}", headers=A_HEADERS).status_code
        == 204
    )
    # 已删除后再删 → 404（限定本家庭，异家庭/不存在同语义）
    assert (
        client.delete(f"/api/family/budgets/{budget_id}", headers=A_HEADERS).status_code
        == 404
    )


def test_disband_deletes_family_budgets(client, db, family):
    client.put(
        "/api/family/budgets",
        json={"month": MONTH, "category": "餐饮", "amount": 150},
        headers=A_HEADERS,
    )
    family_id = family["family_id"]
    assert client.delete("/api/family", headers=A_HEADERS).status_code == 204
    assert BudgetDAO.list_family(family_id, MONTH) == []


def test_backup_roundtrip_restores_family_budget(client, db, family, tmp_path):
    """备份 → 恢复到全新库：家庭预算随家庭按邀请码重映射，user_id 改写为合成属主"""
    client.put(
        "/api/family/budgets",
        json={"month": MONTH, "category": "餐饮", "amount": 150},
        headers=A_HEADERS,
    )
    data = backup_service.export_backup()
    budget_rows = [b for b in data["budgets"] if b.get("family_id")]
    assert len(budget_rows) == 1
    assert budget_rows[0]["family_id"] == family["family_id"]

    fresh = make_engine(tmp_path / "fresh.db")
    Base.metadata.create_all(fresh)
    previous = _STATE.activate(DBSettings(db_type="sqlite"), fresh)
    if previous is not None:
        previous.dispose()
    try:
        with Session(fresh) as session:
            set_schema_version(session, 10)
            session.commit()
        result = backup_service.restore_backup(data, replace=True)
        assert result["budgets"] == 1
        assert result["skipped"] == 0
        with Session(fresh) as session:
            rows = session.execute(
                text(
                    "SELECT user_id, family_id, amount FROM budgets "
                    "WHERE family_id IS NOT NULL"
                )
            ).all()
        assert len(rows) == 1
        new_family_id, amount = rows[0][1], rows[0][2]
        assert rows[0][0] == family_scope_user(new_family_id)
        assert amount == 150
        # 重映射链完整：新 family_id 与「同邀请码恢复出的家庭」id 一致
        with Session(fresh) as session:
            invite = session.execute(
                text("SELECT invite_code FROM families WHERE id = :fid"),
                {"fid": new_family_id},
            ).scalar()
        assert invite == family["invite_code"]
    finally:
        fresh.dispose()


def test_non_member_cannot_touch_family_budgets(client, db):
    """未加入家庭的账号访问家庭预算 → 404（与家庭信息接口同语义）"""
    with pytest.raises(NotFoundError):
        budget_service.family_overview(USER_A, MONTH)
    # 未加入家庭时服务层统一 404（成员资格校验先于角色校验）
    with pytest.raises(NotFoundError):
        budget_service.upsert_family_budget(
            BudgetUpsert(month=MONTH, category="", amount=100), USER_A
        )
    with pytest.raises(NotFoundError):
        budget_service.delete_family_budget(1, USER_A)
