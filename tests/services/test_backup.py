"""备份与恢复测试：JSON 导出结构、合并/覆盖恢复、格式容错"""

import json
from pathlib import Path

import pytest

from app.core.errors import ValidationError
from app.db.dao.bill_dao import BillDAO
from app.db.dao.asset_dao import AssetDAO
from app.db.dao.ledger_dao import LedgerDAO
from app.services import backup_service, import_service
from tests.conftest import USER_A, USER_B, make_bill_records

A_HEADERS = {"X-Trim-Userid": USER_A}
B_HEADERS = {"X-Trim-Userid": USER_B}
# 备份/恢复是全局影响操作，需要管理员身份（本地无网关头场景视为管理员）
ADMIN_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Isadmin": "true"}


def seed_all():
    BillDAO.insert_many(
        make_bill_records(2, prefix="BK", tags="出差", reimbursed=True, user_id=""),
        USER_A,
    )
    BillDAO.insert_many(make_bill_records(1, prefix="KB"), USER_B)
    AssetDAO.create(
        {
            "snap_date": "2026-09-01",
            "name": "存款",
            "asset_type": "asset",
            "amount": 1000,
        },
        USER_A,
    )


def test_backup_download_shape(client):
    seed_all()
    res = client.get("/api/settings/backup", headers=ADMIN_HEADERS)
    assert res.status_code == 200
    assert "application/json" in res.headers["content-type"]
    data = json.loads(res.content.decode("utf-8"))
    assert data["app"] == "fn-finstat"
    assert data["format_version"] == backup_service.BACKUP_FORMAT_VERSION
    assert len(data["bills"]) == 3
    assert data["bills"][0]["user_id"] in (USER_A, USER_B)
    assert "餐饮" in [c["name"] for c in data["categories"]]
    assert data["assets"][0]["name"] == "存款"
    assert data["budgets"] == []


def test_restore_merge_dedupes(client):
    seed_all()
    backup = backup_service.export_backup()

    # 合并恢复同一份备份 → 唯一键去重，账单无新增
    res = client.post(
        "/api/settings/restore",
        headers=ADMIN_HEADERS,
        data={"replace": "false"},
        files={
            "file": (
                "backup.json",
                json.dumps(backup).encode("utf-8"),
                "application/json",
            )
        },
    )
    assert res.status_code == 200
    body = res.json()["data"]
    assert body["replaced"] is False
    assert body["bills"] == 3  # 提交 3 条（带 tx_id 全部去重）
    rows = client.get("/api/bill/list?page_size=50", headers=A_HEADERS).json()["data"]
    assert rows["total"] == 2  # 列表按账号隔离：A 只见自己的 2 条
    assert (
        client.get("/api/bill/list?page_size=50", headers=B_HEADERS).json()["data"][
            "total"
        ]
        == 1
    )

    # 无交易号流水不去重，重复恢复会产生重复（文档化行为）
    backup["bills"] = [{**backup["bills"][0], "tx_id": None}]
    res = client.post(
        "/api/settings/restore",
        headers=ADMIN_HEADERS,
        files={
            "file": ("b.json", json.dumps(backup).encode("utf-8"), "application/json")
        },
    )
    assert res.status_code == 200
    rows = client.get("/api/bill/list?page_size=50", headers=A_HEADERS).json()["data"]
    assert rows["total"] == 3


def test_restore_replace_wipes_existing(client):
    seed_all()
    backup = backup_service.export_backup()
    # 只保留一条账单做覆盖恢复
    backup["bills"] = [backup["bills"][0]]

    res = client.post(
        "/api/settings/restore",
        headers=ADMIN_HEADERS,
        data={"replace": "true"},
        files={
            "file": ("b.json", json.dumps(backup).encode("utf-8"), "application/json")
        },
    )
    assert res.status_code == 200
    assert res.json()["data"]["replaced"] is True

    rows_a = client.get("/api/bill/list?page_size=50", headers=A_HEADERS).json()["data"]
    rows_b = client.get("/api/bill/list?page_size=50", headers=B_HEADERS).json()["data"]
    assert rows_a["total"] == 1 and rows_b["total"] == 0
    assets = client.get("/api/asset", headers=A_HEADERS).json()["data"]
    assert len(assets) == 1  # assets 节仍恢复
    assert (
        client.get("/api/bill/recycle", headers=A_HEADERS).json()["data"]["total"] == 0
    )


def test_restore_invalid_and_malformed_rows(client):
    res = client.post(
        "/api/settings/restore",
        headers=ADMIN_HEADERS,
        files={"file": ("b.json", b"not json", "application/json")},
    )
    assert res.status_code == 400

    res = client.post(
        "/api/settings/restore",
        headers=ADMIN_HEADERS,
        files={
            "file": (
                "b.json",
                json.dumps({"foo": 1}).encode("utf-8"),
                "application/json",
            )
        },
    )
    assert res.status_code == 400

    backup = {
        "bills": [
            {
                "tx_time": "2026-01-01 10:00:00",
                "tx_type": "expense",
                "amount": 10,
                "category": "新分类",
                "tx_id": "NEW-1",
            },
            {"tx_time": "", "tx_type": "expense", "amount": 5},  # 缺时间 → 跳过
            {
                "tx_time": "2026-01-02 10:00:00",
                "tx_type": "xxx",
                "amount": 5,
            },  # 非法类型 → 跳过
        ]
    }
    res = client.post(
        "/api/settings/restore",
        headers=ADMIN_HEADERS,
        files={
            "file": ("b.json", json.dumps(backup).encode("utf-8"), "application/json")
        },
    )
    body = res.json()["data"]
    assert body["bills"] == 1 and body["skipped"] == 2
    # 流水引用的「新分类」自动补建
    names = [
        c["name"] for c in client.get("/api/category", headers=A_HEADERS).json()["data"]
    ]
    assert "新分类" in names


def test_backup_requires_admin(client):
    """多账号模式下备份/恢复仅限管理员（本地无网关头不受影响）"""
    seed_all()
    res = client.get("/api/settings/backup", headers=A_HEADERS)
    assert res.status_code == 403
    res = client.post(
        "/api/settings/restore",
        files={"file": ("b.json", b"{}", "application/json")},
        headers=A_HEADERS,
    )
    assert res.status_code == 403
    # 本地模式（无任何网关头）视为唯一用户，不受限
    assert client.get("/api/settings/backup").status_code == 200


def test_import_rejected_during_restore(db):
    """评审 M-10：恢复进行中的新导入被拒绝——写入「穿越」清空点会让最终
    状态既非纯备份也非纯现况（RESTORE_LOCK 由恢复与导入两侧共用）"""

    class _FakeParser:
        def parse(self, path):
            return []

    with backup_service.RESTORE_LOCK:
        with pytest.raises(ValidationError):
            import_service.import_local_file(
                Path("x.csv"), _FakeParser(), "x.csv", USER_A
            )


def _backup_with_renamed_default() -> dict:
    """构造「默认账本被改名为主账本」的备份：is_default 标记随名走，id 与本库无关"""
    return {
        "categories": ["餐饮"],
        "ledgers": [
            {
                "id": 1,
                "name": "主账本",
                "owner_id": "",
                "is_default": True,
                "remark": "改名过的默认账本",
            },
            {
                "id": 2,
                "name": "旅行",
                "owner_id": "",
                "is_default": False,
                "remark": "",
            },
        ],
        "bills": [
            {
                "user_id": USER_A,
                "tx_time": "2026-01-01 10:00:00",
                "tx_type": "expense",
                "amount": 100,
                "category": "餐饮",
                "tx_id": "RS-DEF-1",
                "ledger_id": 2,
            }
        ],
    }


def test_restore_merge_converges_single_default(db):
    """合并恢复：备份带来的第二个 is_default=True 让位给本地默认账本

    is_default 无唯一约束（评审 P2）：改名过的默认账本合并进已有默认账本的库时，
    若两个标记都保留，ensure_default_ledger 的命中将不确定。收口后全局唯一默认，
    且备份流水仍按名归入「旅行」，不受默认标记降级影响。
    """
    result = backup_service.restore_backup(_backup_with_renamed_default())
    assert result["ledgers"] == 2

    ledgers = LedgerDAO.list_with_counts()
    defaults = [l for l in ledgers if l["is_default"]]
    assert len(defaults) == 1 and defaults[0]["name"] == "默认账本"

    travel = next(l for l in ledgers if l["name"] == "旅行")
    assert travel["bill_count"] == 1
    total, rows = BillDAO.list_bills(USER_A, page_size=10)
    assert total == 1 and rows[0]["ledger_id"] == travel["id"]


def test_restore_replace_keeps_backup_default(db):
    """覆盖恢复：库已清空，默认归属以备份为准——改名过的默认账本仍是唯一默认"""
    backup_service.restore_backup(_backup_with_renamed_default(), replace=True)

    ledgers = LedgerDAO.list_with_counts()
    defaults = [l for l in ledgers if l["is_default"]]
    assert len(defaults) == 1 and defaults[0]["name"] == "主账本"

    travel = next(l for l in ledgers if l["name"] == "旅行")
    _, rows = BillDAO.list_bills(USER_A, page_size=10)
    assert len(rows) == 1 and rows[0]["ledger_id"] == travel["id"]


def test_family_backup_roundtrip(db):
    """家庭随备份导出并在覆盖恢复后完整还原：成员行按邀请码重映射到新家庭 id"""
    from app.db.dao.family_dao import FamilyDAO

    family = FamilyDAO.create("备份之家", USER_A, nickname="张三")
    FamilyDAO.join(family["id"], USER_B, nickname="李四")

    backup = backup_service.export_backup()
    assert {m["user_id"] for m in backup["family_members"]} == {USER_A, USER_B}

    # 覆盖恢复到空库（本测试自用独立库，replace 前先造一点残留验证清理）
    FamilyDAO.create("残留家庭", "ghost")
    result = backup_service.restore_backup(backup, replace=True)
    assert result["families"] == 1 and result["family_members"] == 2

    restored = FamilyDAO.member_of(USER_A)
    assert restored is not None
    # SQLite 覆盖恢复后新家庭可能复用原 id（rowid 重新从 1 起），只要成员行
    # 完整挂在新家庭下且按邀请码对齐即视为还原成功
    assert restored["role"] == "admin" and restored["nickname"] == "张三"
    assert FamilyDAO.count_members(restored["family_id"]) == 2
    assert {m["user_id"] for m in FamilyDAO.list_members(restored["family_id"])} == {
        USER_A,
        USER_B,
    }


def _seed_new_module_rows():
    """造四张新业务表的数据：储蓄目标 + 借条/还款 + 报销单挂流水（返回报销单 id）"""
    from app.db.dao.loan_dao import LoanDAO
    from app.db.dao.reimb_dao import ReimbDAO
    from app.db.dao.savings_dao import SavingsGoalDAO

    SavingsGoalDAO.create(
        USER_A,
        {
            "name": "应急金",
            "target_amount": 20000,
            "start_date": "2026-01-01",
            "target_date": "2026-12-31",
        },
    )
    loan = LoanDAO.create(
        USER_A,
        {
            "direction": "lend",
            "counterparty": "老王",
            "principal": 1000,
            "loan_date": "2026-02-01",
        },
    )
    LoanDAO.add_payment(loan["id"], {"amount": 400, "pay_date": "2026-03-01"})
    claim = ReimbDAO.create(USER_A, "出差报销", "高铁票")
    BillDAO.insert_many(
        make_bill_records(2, prefix="RB", category="交通", user_id=USER_A), USER_A
    )
    _, rows = BillDAO.list_bills(USER_A, page_size=10)
    ReimbDAO.attach_bills(claim["id"], USER_A, [r["id"] for r in rows])
    return claim["id"]


def test_new_tables_backup_roundtrip_and_replace(db):
    """四张新业务表进备份（v6）：覆盖恢复清空后精确还原，不翻倍不残留（P0）

    此前 savings_goals 不在备份节、replace 清空清单漏四张新表——恢复后目标
    消失、借贷/报销翻倍。本测试钉住完整往返语义。
    """
    from sqlalchemy import func, select

    from app.db.dao.loan_dao import LoanDAO
    from app.db.dao.reimb_dao import ReimbDAO
    from app.db.dao.savings_dao import SavingsGoalDAO
    from app.db.engine import _STATE
    from app.db.models import Loan, LoanPayment, Reimbursement, SavingsGoal

    claim_id = _seed_new_module_rows()
    backup = backup_service.export_backup()
    assert len(backup["savings_goals"]) == 1
    assert len(backup["loans"]) == 1 and len(backup["loan_payments"]) == 1
    assert len(backup["reimbursements"]) == 1

    # 覆盖恢复前先造残留：replace 必须把旧的四表行全部清掉
    LoanDAO.create(
        USER_B,
        {
            "direction": "borrow",
            "counterparty": "残留借条",
            "principal": 1,
            "loan_date": "2026-01-01",
        },
    )
    SavingsGoalDAO.create(
        USER_B, {"name": "残留目标", "target_amount": 1, "start_date": "2026-01-01"}
    )

    result = backup_service.restore_backup(backup, replace=True)
    assert result["loans"] == 1 and result["loan_payments"] == 1
    assert result["reimbursements"] == 1 and result["savings_goals"] == 1

    session = _STATE.new_session()
    assert session.scalar(select(func.count()).select_from(Loan)) == 1
    assert session.scalar(select(func.count()).select_from(LoanPayment)) == 1
    assert session.scalar(select(func.count()).select_from(Reimbursement)) == 1
    assert session.scalar(select(func.count()).select_from(SavingsGoal)) == 1
    # 流水的报销关联在覆盖恢复后按业务键重映射到新报销单，不再是悬挂引用
    _, rows = BillDAO.list_bills(USER_A, page_size=10)
    assert all(r["reimb_id"] == claim_id for r in rows)
    assert ReimbDAO.list_bills(claim_id, USER_A)


def test_new_tables_merge_restore_does_not_double(db):
    """覆盖恢复后重复合并恢复同一备份：借条/还款/报销/储蓄按业务键去重不翻倍"""
    from sqlalchemy import func, select

    from app.db.engine import _STATE
    from app.db.models import Loan, LoanPayment, Reimbursement, SavingsGoal

    _seed_new_module_rows()
    backup = backup_service.export_backup()

    # 先覆盖恢复到干净库（seed 行被清掉），再重复合并恢复同一备份
    first = backup_service.restore_backup(backup, replace=True)
    second = backup_service.restore_backup(backup)
    assert first["loans"] == 1 and second["loans"] == 0
    assert first["loan_payments"] == 1 and second["loan_payments"] == 0
    assert first["reimbursements"] == 1 and second["reimbursements"] == 0
    assert first["savings_goals"] == 1 and second["savings_goals"] == 0

    session = _STATE.new_session()
    assert session.scalar(select(func.count()).select_from(Loan)) == 1
    assert session.scalar(select(func.count()).select_from(LoanPayment)) == 1
    assert session.scalar(select(func.count()).select_from(Reimbursement)) == 1
    assert session.scalar(select(func.count()).select_from(SavingsGoal)) == 1


def test_restore_legacy_bill_without_user_id(db):
    """0.2.x 时代备份（流水缺 user_id 键）恢复后归入默认账号，不再静默丢行"""
    backup = {
        "bills": [
            {
                "tx_time": "2025-05-01 10:00:00",
                "tx_type": "expense",
                "amount": 12.5,
                "category": "餐饮",
                "tx_id": "LEGACY-1",
            }
        ]
    }
    result = backup_service.restore_backup(backup)
    assert result["bills"] == 1 and result["skipped"] == 0
    _, rows = BillDAO.list_bills("", page_size=10)
    assert any(r["tx_id"] == "LEGACY-1" for r in rows)
