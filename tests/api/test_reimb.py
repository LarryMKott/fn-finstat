"""报销 / 垫付工作流测试（T-7.4）：状态机、挂摘单、标记同步、隐私与备份往返

行为底线：
- 状态机 待提交 → 已提交 → 部分到账 → 已结清；部分到账 / 已结清必须登记
  到账金额，回到待提交 / 已提交时到账登记清空
- 只有支出流水可挂单；回收站与其他报销单内的流水被拒绝
- bills.reimbursed 标记随挂/摘同步（既有报销筛选零破坏）
- 报销单按账号隔离；流水进回收站自动摘除关联
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import DBSettings
from app.db.base import set_schema_version
from app.db.dao.bill_dao import BillDAO
from app.db.engine import _STATE
from app.db.models import Base
from app.services import backup_service
from tests.conftest import USER_A, USER_B, make_bill_records, make_engine

A_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Username": "zhangsan"}
B_HEADERS = {"X-Trim-Userid": USER_B, "X-Trim-Username": "lisi"}


def _expense(prefix, amount, tx_time="2026-09-05 10:00:00", **overrides):
    return make_bill_records(
        1,
        prefix=prefix,
        tx_time=tx_time,
        amount=amount,
        category="餐饮",
        **overrides,
    )


def _create_claim(client, title="出差报销"):
    res = client.post("/api/reimb", json={"title": title}, headers=A_HEADERS)
    assert res.status_code == 201
    return res.json()["data"]


def _bill_id_by_tx(tx_id: str) -> int:
    from sqlalchemy import select

    from app.db.base import get_db
    from app.db.models import Bill

    with get_db() as session:
        return session.scalar(select(Bill.id).where(Bill.tx_id == tx_id))


def _seed_bills():
    """插三笔流水并返回真实 id（insert_many 的返回值是条数不是 id）"""
    BillDAO.insert_many(_expense("RB-E", 88.0), USER_A)
    BillDAO.insert_many(_expense("RB-E2", 12.0), USER_A)
    BillDAO.insert_many(
        make_bill_records(
            1,
            prefix="RB-I",
            tx_time="2026-09-06 10:00:00",
            amount=50.0,
            tx_type="income",
            category="其他",
        ),
        USER_A,
    )
    return {
        "expense": _bill_id_by_tx("RB-E-0000"),
        "expense2": _bill_id_by_tx("RB-E2-0000"),
        "income": _bill_id_by_tx("RB-I-0000"),
    }


def test_claim_crud_and_status_machine(client, db):
    claim = _create_claim(client)
    assert claim["status"] == "pending"
    assert claim["status_label"] == "待提交"
    assert claim["bill_count"] == 0

    claim_id = claim["id"]
    # 不存在的流水 id：明确 400 而不是静默吞掉
    res = client.post(
        f"/api/reimb/{claim_id}/bills", json={"ids": [0]}, headers=A_HEADERS
    )
    assert res.status_code == 400

    bills = _seed_bills()
    res = client.post(
        f"/api/reimb/{claim_id}/bills",
        json={"ids": [bills["expense"], bills["expense2"]]},
        headers=A_HEADERS,
    )
    assert res.status_code == 200
    assert res.json()["data"]["attached"] == 2

    # 报销标记同步：reimbursed=1，既有筛选可见
    flags = client.get(
        "/api/bill/list", params={"reimbursed": "true"}, headers=A_HEADERS
    ).json()["data"]
    assert flags["total"] == 2

    # 待提交 → 已提交（无需到账金额）
    res = client.put(
        f"/api/reimb/{claim_id}", json={"status": "submitted"}, headers=A_HEADERS
    )
    assert res.status_code == 200
    assert res.json()["data"]["status_label"] == "已提交"

    # 已提交 → 部分到账：缺到账金额被拒绝
    bad = client.put(
        f"/api/reimb/{claim_id}", json={"status": "partial"}, headers=A_HEADERS
    )
    assert bad.status_code == 400
    partial = client.put(
        f"/api/reimb/{claim_id}",
        json={
            "status": "partial",
            "received_amount": 50,
            "received_date": "2026-09-20",
        },
        headers=A_HEADERS,
    )
    assert partial.status_code == 200
    assert partial.json()["data"]["received_amount"] == 50

    # 部分到账 → 已结清
    settled = client.put(
        f"/api/reimb/{claim_id}",
        json={"status": "settled", "received_amount": 100},
        headers=A_HEADERS,
    )
    assert settled.status_code == 200
    assert settled.json()["data"]["status_label"] == "已结清"

    # 回到已提交：到账登记被清空
    back = client.put(
        f"/api/reimb/{claim_id}", json={"status": "submitted"}, headers=A_HEADERS
    )
    assert back.status_code == 200
    assert back.json()["data"]["received_amount"] is None

    # 明细：笔数与金额合计
    detail = client.get(f"/api/reimb/{claim_id}/bills", headers=A_HEADERS).json()[
        "data"
    ]
    assert detail["count"] == 2
    assert detail["total"] == 100

    # 列表聚合
    claims = client.get("/api/reimb", headers=A_HEADERS).json()["data"]
    mine = [c for c in claims if c["id"] == claim_id][0]
    assert mine["bill_count"] == 2
    assert mine["total_amount"] == 100


def test_attach_rejects_income_deleted_and_other_claim(client, db):
    ids = _seed_bills()
    first = _create_claim(client, "第一张")
    second = _create_claim(client, "第二张")

    # 收入流水不可挂
    res = client.post(
        f"/api/reimb/{first['id']}/bills",
        json={"ids": [ids["income"]]},
        headers=A_HEADERS,
    )
    assert res.status_code == 400

    # 挂单后不可重复挂到其他报销单
    client.post(
        f"/api/reimb/{first['id']}/bills",
        json={"ids": [ids["expense"]]},
        headers=A_HEADERS,
    )
    dup = client.post(
        f"/api/reimb/{second['id']}/bills",
        json={"ids": [ids["expense"]]},
        headers=A_HEADERS,
    )
    assert dup.status_code == 400

    # 回收站流水不可挂
    BillDAO.set_deleted_flag([ids["expense2"]], USER_A, True)
    recycled = client.post(
        f"/api/reimb/{second['id']}/bills",
        json={"ids": [ids["expense2"]]},
        headers=A_HEADERS,
    )
    assert recycled.status_code == 400


def test_claim_privacy_between_accounts(client, db):
    ids = BillDAO.insert_many(_expense("RB-P", 66.0), USER_A)
    claim = _create_claim(client)
    client.post(
        f"/api/reimb/{claim['id']}/bills", json={"ids": [ids]}, headers=A_HEADERS
    )
    # B 看 / 改 / 删 A 的报销单一律 404（按账号隔离，不暴露存在性）
    assert (
        client.get(f"/api/reimb/{claim['id']}/bills", headers=B_HEADERS).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/reimb/{claim['id']}",
            json={"status": "submitted"},
            headers=B_HEADERS,
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/reimb/{claim['id']}", headers=B_HEADERS).status_code == 404
    )
    # B 的列表为空
    assert client.get("/api/reimb", headers=B_HEADERS).json()["data"] == []


def test_recycle_bin_detaches_claim(client, db):
    bill_id = BillDAO.insert_many(_expense("RB-D", 30.0), USER_A)
    claim = _create_claim(client, "删除联动")
    client.post(
        f"/api/reimb/{claim['id']}/bills", json={"ids": [bill_id]}, headers=A_HEADERS
    )
    # 软删除 → 自动摘除关联（报销单明细与列表聚合同步变化）
    client.delete(f"/api/bill/{bill_id}", headers=A_HEADERS)
    detail = client.get(f"/api/reimb/{claim['id']}/bills", headers=A_HEADERS).json()[
        "data"
    ]
    assert detail["count"] == 0
    claims = client.get("/api/reimb", headers=A_HEADERS).json()["data"]
    assert claims[0]["bill_count"] == 0


def test_delete_claim_detaches_and_resets_flag(client, db):
    bill_id = BillDAO.insert_many(_expense("RB-X", 77.0), USER_A)
    claim = _create_claim(client, "删除摘单")
    client.post(
        f"/api/reimb/{claim['id']}/bills", json={"ids": [bill_id]}, headers=A_HEADERS
    )
    assert (
        client.delete(f"/api/reimb/{claim['id']}", headers=A_HEADERS).status_code == 204
    )
    with Session(_active_engine()) as session:
        reimbursed, reimb_id = session.execute(
            text("SELECT reimbursed, reimb_id FROM bills WHERE id = :bid"),
            {"bid": bill_id},
        ).first()
    assert reimbursed == 0 and reimb_id is None


def test_backup_roundtrip_restores_claims(client, db, tmp_path):
    bill_id = BillDAO.insert_many(_expense("RB-BK", 123.0), USER_A)
    claim = _create_claim(client, "备份往返")
    client.post(
        f"/api/reimb/{claim['id']}/bills", json={"ids": [bill_id]}, headers=A_HEADERS
    )
    client.put(
        f"/api/reimb/{claim['id']}",
        json={"status": "partial", "received_amount": 60},
        headers=A_HEADERS,
    )
    data = backup_service.export_backup()
    assert len(data["reimbursements"]) == 1
    assert data["reimbursements"][0]["status"] == "partial"

    fresh = make_engine(tmp_path / "fresh.db")
    Base.metadata.create_all(fresh)
    previous = _STATE.activate(DBSettings(db_type="sqlite"), fresh)
    if previous is not None:
        previous.dispose()
    try:
        with Session(fresh) as session:
            set_schema_version(session, 11)
            session.commit()
        result = backup_service.restore_backup(data, replace=True)
        assert result["reimbursements"] == 1
        assert result["skipped"] == 0
        with Session(fresh) as session:
            row = session.execute(
                text(
                    "SELECT b.reimb_id, r.status, r.received_amount FROM bills b "
                    "JOIN reimbursements r ON b.reimb_id = r.id"
                )
            ).first()
        assert row is not None
        assert row[1] == "partial" and row[2] == 60
    finally:
        fresh.dispose()


def _active_engine():
    return _STATE.engine()
