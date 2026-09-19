"""借贷台账测试（T-7.5）：借出 / 借入登记、还款进度、还清即结项、隐私与备份往返

行为底线：
- 台账独立于流水（不与 bills 关联），应收 / 应付汇总 = 各条 remaining 之和
- 「还清即结项」：还款合计 >= 本金自动置已结清，删除还款后不足回到进行中
- 数据按账号隔离；备份往返贯通 loans / loan_payments
"""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import DBSettings
from app.core.errors import NotFoundError
from app.db.base import set_schema_version
from app.db.engine import _STATE
from app.db.models import Base
from app.services import backup_service, loan_service
from tests.conftest import USER_A, USER_B, make_engine

A_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Username": "zhangsan"}
B_HEADERS = {"X-Trim-Userid": USER_B, "X-Trim-Username": "lisi"}


def _create_loan(
    client, title_kw="老王", direction="lend", principal=500.0, **overrides
):
    payload = {
        "direction": direction,
        "counterparty": title_kw,
        "principal": principal,
        "loan_date": "2026-09-01",
        "due_date": "2026-10-01",
        **overrides,
    }
    res = client.post("/api/loans", json=payload, headers=A_HEADERS)
    assert res.status_code == 201, res.text
    return res.json()["data"]


def test_create_and_ledger_summary(client, db):
    _create_loan(client, direction="lend", principal=500.0)
    _create_loan(client, title_kw="银行", direction="borrow", principal=200.0)
    data = client.get("/api/loans", headers=A_HEADERS).json()["data"]
    assert data["receivable"] == 500
    assert data["payable"] == 200
    by_cp = {i["counterparty"]: i for i in data["items"]}
    assert by_cp["老王"]["direction_label"] == "借出（应收）"
    assert by_cp["银行"]["direction_label"] == "借入（应付）"


def test_payment_flow_auto_settles(client, db):
    loan = _create_loan(client, principal=500.0)
    loan_id = loan["id"]
    assert loan["status"] == "open" and loan["remaining"] == 500

    first = client.post(
        f"/api/loans/{loan_id}/payments",
        json={"amount": 200, "pay_date": "2026-09-15", "note": "第一笔"},
        headers=A_HEADERS,
    )
    assert first.status_code == 200
    progress = first.json()["data"]
    assert progress["repaid"] == 200
    assert progress["remaining"] == 300
    assert progress["status"] == "open"  # 未还清，仍进行中

    second = client.post(
        f"/api/loans/{loan_id}/payments",
        json={"amount": 300, "pay_date": "2026-09-20"},
        headers=A_HEADERS,
    )
    progress = second.json()["data"]
    assert progress["status"] == "settled"  # 还清即结项
    assert progress["remaining"] == 0

    # 删除一笔还款：合计不足本金 → 回到进行中
    payment_id = progress["rows"][0]["id"]
    reopened = client.delete(
        f"/api/loans/{loan_id}/payments/{payment_id}", headers=A_HEADERS
    )
    assert reopened.status_code == 200
    assert reopened.json()["data"]["status"] == "open"


def test_payment_and_loan_validation(client, db):
    loan = _create_loan(client, principal=500.0)
    loan_id = loan["id"]
    # 无效方向 / 非正本金：schema 层 Literal 与 gt=0 校验 → 422
    assert (
        client.post(
            "/api/loans",
            json={
                "direction": "steal",
                "counterparty": "X",
                "principal": 1,
                "loan_date": "2026-09-01",
            },
            headers=A_HEADERS,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/loans",
            json={
                "direction": "lend",
                "counterparty": "X",
                "principal": 0,
                "loan_date": "2026-09-01",
            },
            headers=A_HEADERS,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/loans",
            json={
                "direction": "lend",
                "counterparty": "  ",
                "principal": 1,
                "loan_date": "2026-09-01",
            },
            headers=A_HEADERS,
        ).status_code
        == 400
    )
    # 还款金额必须 > 0（schema 层 gt=0 → 422）；日期非法（服务层 → 400）
    assert (
        client.post(
            f"/api/loans/{loan_id}/payments",
            json={"amount": 0, "pay_date": "2026-09-15"},
            headers=A_HEADERS,
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/api/loans/{loan_id}/payments",
            json={"amount": 10, "pay_date": "2026-09-"},
            headers=A_HEADERS,
        ).status_code
        == 400
    )
    # 不存在的借贷 / 还款 → 404
    assert (
        client.post(
            "/api/loans/999/payments",
            json={"amount": 10, "pay_date": "2026-09-15"},
            headers=A_HEADERS,
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/loans/{loan_id}/payments/999", headers=A_HEADERS
        ).status_code
        == 404
    )


def test_loan_privacy_between_accounts(client, db):
    loan = _create_loan(client, principal=500.0)
    assert client.get("/api/loans", headers=B_HEADERS).json()["data"]["items"] == []
    assert (
        client.delete(f"/api/loans/{loan['id']}", headers=B_HEADERS).status_code == 404
    )
    with pytest.raises(NotFoundError):
        loan_service.list_payments(loan["id"], USER_B)


def test_update_loan_and_progress_recalc(client, db):
    loan = _create_loan(client, principal=500.0)
    loan_id = loan["id"]
    client.post(
        f"/api/loans/{loan_id}/payments",
        json={"amount": 500, "pay_date": "2026-09-30"},
        headers=A_HEADERS,
    )
    assert (
        client.get("/api/loans", headers=A_HEADERS).json()["data"]["items"][0]["status"]
        == "settled"
    )
    # 本金调高：还款不足 → 回到进行中（进度按新本金重推导）
    res = client.put(
        f"/api/loans/{loan_id}", json={"principal": 800}, headers=A_HEADERS
    )
    updated = res.json()["data"]
    assert updated["principal"] == 800
    assert updated["status"] == "open"
    assert updated["remaining"] == 300


def test_backup_roundtrip_restores_loans(client, db, tmp_path):
    loan = _create_loan(client, principal=500.0)
    client.post(
        f"/api/loans/{loan['id']}/payments",
        json={"amount": 200, "pay_date": "2026-09-15", "note": "第一笔"},
        headers=A_HEADERS,
    )
    data = backup_service.export_backup()
    assert len(data["loans"]) == 1
    assert len(data["loan_payments"]) == 1

    fresh = make_engine(tmp_path / "fresh.db")
    Base.metadata.create_all(fresh)
    previous = _STATE.activate(DBSettings(db_type="sqlite"), fresh)
    if previous is not None:
        previous.dispose()
    try:
        with Session(fresh) as session:
            set_schema_version(session, 12)
            session.commit()
        result = backup_service.restore_backup(data, replace=True)
        assert result["loans"] == 1
        assert result["loan_payments"] == 1
        assert result["skipped"] == 0
        with Session(fresh) as session:
            loan_row = session.execute(
                text(
                    "SELECT id, counterparty, principal FROM loans "
                    "WHERE counterparty = '老王'"
                )
            ).first()
            payment = session.execute(
                text(
                    "SELECT lp.loan_id, lp.amount FROM loan_payments lp "
                    "JOIN loans l ON lp.loan_id = l.id"
                )
            ).first()
        assert loan_row[1] == "老王" and loan_row[2] == 500
        assert payment is not None
        assert payment[0] == loan_row[0]  # loan_id 重映射到新 id
        assert payment[1] == 200
    finally:
        fresh.dispose()
