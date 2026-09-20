"""借贷台账业务逻辑（T-7.5，按当前飞牛账号隔离）

借出（应收）/ 借入（应付）的本金与还款跟踪：独立小台账，与流水不强制
关联；「还清即结项」——还款合计 >= 本金时状态自动置为已结清，删除还款
后若不足则回到进行中。
"""

from app.core.constants import (
    LOAN_DIRECTION_LABELS,
    LOAN_STATUSES,
)
from app.core.errors import ErrorCode, NotFoundError, ValidationError
from app.db.dao.loan_dao import LoanDAO
from app.services import audit_service
from app.utils.amount import normalize_amount, round2
from app.utils.period import valid_date as _valid_date

_NOTE_MAX = 255


def _derive_status(principal: float, repaid: float) -> str:
    """还清即结项：还款合计 >= 本金（本金>0）自动置已结清"""
    return "settled" if principal > 0 and repaid + 1e-9 >= principal else "open"


def _with_progress(loan: dict, repaid: float, payment_count: int) -> dict:
    return {
        **loan,
        "repaid": round2(repaid),
        "remaining": round2(max(loan["principal"] - repaid, 0)),
        "payment_count": payment_count,
        "direction_label": LOAN_DIRECTION_LABELS.get(
            loan["direction"], loan["direction"]
        ),
        "status": loan["status"] if loan["status"] in LOAN_STATUSES else "open",
    }


def list_loans(user_id: str) -> dict:
    """借贷台账全量（含进度），并给出应收 / 应付汇总"""
    rows = LoanDAO.list_with_progress(user_id)
    for r in rows:
        r["remaining"] = round2(max(r["principal"] - r["repaid"], 0))
        r["direction_label"] = LOAN_DIRECTION_LABELS.get(r["direction"], r["direction"])
    receivable = round2(sum(r["remaining"] for r in rows if r["direction"] == "lend"))
    payable = round2(sum(r["remaining"] for r in rows if r["direction"] == "borrow"))
    return {
        "receivable": receivable,
        "payable": payable,
        "items": rows,
    }


def create_loan(payload, user_id: str) -> dict:
    """登记一笔借出 / 借入"""
    direction = payload.direction
    if direction not in ("lend", "borrow"):
        raise ValidationError("无效的借贷方向", code=ErrorCode.LOAN_INVALID)
    counterparty = (payload.counterparty or "").strip()
    if not counterparty:
        raise ValidationError("请填写对方名称", code=ErrorCode.LOAN_INVALID)
    principal = normalize_amount(payload.principal)
    if principal <= 0:
        raise ValidationError("本金必须大于 0", code=ErrorCode.LOAN_INVALID)
    loan_date = _valid_date(payload.loan_date, "借贷日期")
    due_date = payload.due_date or None
    if due_date:
        due_date = _valid_date(due_date, "约定还款日")
    loan = LoanDAO.create(
        user_id,
        {
            "direction": direction,
            "counterparty": counterparty[:64],
            "principal": principal,
            "loan_date": loan_date,
            "due_date": due_date,
            "note": (payload.note or "").strip()[:_NOTE_MAX],
            "status": "open",
        },
    )
    audit_service.record(
        user_id,
        "loan.create",
        "loan",
        loan["id"],
        LOAN_DIRECTION_LABELS[direction]
        + "："
        + counterparty
        + " "
        + str(principal)
        + " 元",
    )
    return _with_progress(loan, 0, 0)


def _require_loan(loan_id: int, user_id: str) -> dict:
    loan = LoanDAO.get(loan_id, user_id)
    if loan is None:
        raise NotFoundError("借贷记录不存在", code=ErrorCode.LOAN_NOT_FOUND)
    return loan


def update_loan(loan_id: int, payload, user_id: str) -> dict:
    """更新借贷信息（对方 / 本金 / 日期 / 备注），本金变化后重新推导状态"""
    loan = _require_loan(loan_id, user_id)
    fields: dict = {}
    if payload.counterparty is not None:
        cleaned = payload.counterparty.strip()
        if not cleaned:
            raise ValidationError("请填写对方名称", code=ErrorCode.LOAN_INVALID)
        fields["counterparty"] = cleaned[:64]
    if payload.principal is not None:
        principal = normalize_amount(payload.principal)
        if principal <= 0:
            raise ValidationError("本金必须大于 0", code=ErrorCode.LOAN_INVALID)
        fields["principal"] = principal
    if payload.loan_date is not None:
        fields["loan_date"] = _valid_date(payload.loan_date, "借贷日期")
    if payload.due_date is not None:
        fields["due_date"] = (
            _valid_date(payload.due_date, "约定还款日") if payload.due_date else None
        )
    if payload.note is not None:
        fields["note"] = payload.note.strip()[:_NOTE_MAX]
    updated = LoanDAO.update_fields(loan_id, user_id, fields)
    if updated is None:
        raise NotFoundError("借贷记录不存在", code=ErrorCode.LOAN_NOT_FOUND)
    repaid = LoanDAO.repaid_total(loan_id, user_id)
    status = _derive_status(updated["principal"], repaid)
    if status != updated["status"]:
        updated = LoanDAO.update_fields(loan_id, user_id, {"status": status})
    diff = audit_service.diff_summary(
        loan,
        updated,
        {
            "counterparty": "对方",
            "principal": "本金",
            "loan_date": "借贷日期",
            "due_date": "还款日",
            "note": "备注",
        },
    )
    audit_service.record(
        user_id,
        "loan.update",
        "loan",
        loan_id,
        "更新借贷 " + updated["counterparty"] + "：" + (diff or "无字段变化"),
    )
    return _with_progress(updated, repaid, 0)


def delete_loan(loan_id: int, user_id: str) -> None:
    """删除借贷及其全部还款记录"""
    loan = _require_loan(loan_id, user_id)
    if not LoanDAO.delete(loan_id, user_id):
        raise NotFoundError("借贷记录不存在", code=ErrorCode.LOAN_NOT_FOUND)
    audit_service.record(
        user_id,
        "loan.delete",
        "loan",
        loan_id,
        "删除借贷：" + loan["counterparty"] + " " + str(loan["principal"]) + " 元",
    )


def _refresh_status(loan_id: int, user_id: str) -> dict:
    loan = _require_loan(loan_id, user_id)
    repaid = LoanDAO.repaid_total(loan_id, user_id)
    status = _derive_status(loan["principal"], repaid)
    if status != loan["status"]:
        loan = LoanDAO.update_fields(loan_id, user_id, {"status": status}) or loan
    return _with_progress(loan, repaid, LoanDAO.list_payments(loan_id).__len__())


def list_payments(loan_id: int, user_id: str) -> dict:
    """还款明细（含进度）"""
    loan = _require_loan(loan_id, user_id)
    rows = LoanDAO.list_payments(loan_id)
    repaid = round2(sum(p["amount"] for p in rows))
    return {
        "loan_id": loan_id,
        "principal": round2(loan["principal"]),
        "repaid": repaid,
        "remaining": round2(max(loan["principal"] - repaid, 0)),
        "status": _derive_status(loan["principal"], repaid),
        "rows": rows,
    }


def add_payment(loan_id: int, payload, user_id: str) -> dict:
    """登记一笔还款；合计达到本金自动结清"""
    _require_loan(loan_id, user_id)
    amount = normalize_amount(payload.amount)
    if amount <= 0:
        raise ValidationError("还款金额必须大于 0", code=ErrorCode.LOAN_INVALID)
    pay_date = _valid_date(payload.pay_date, "还款日期")
    loan = _require_loan(loan_id, user_id)
    LoanDAO.add_payment(
        loan_id,
        {
            "amount": amount,
            "pay_date": pay_date,
            "note": (payload.note or "").strip()[:_NOTE_MAX],
        },
    )
    progress = _refresh_status(loan_id, user_id)  # 还清即结项：状态落库
    audit_service.record(
        user_id,
        "loan.payment_add",
        "loan",
        loan_id,
        loan["counterparty"]
        + " 还款 "
        + str(amount)
        + " 元"
        + ("（已全部结清）" if progress["status"] == "settled" else ""),
    )
    return list_payments(loan_id, user_id)


def delete_payment(loan_id: int, payment_id: int, user_id: str) -> dict:
    """删除一条还款记录；合计低于本金后状态回到进行中"""
    _require_loan(loan_id, user_id)
    if not LoanDAO.get_payment(payment_id, loan_id):
        raise NotFoundError("还款记录不存在", code=ErrorCode.LOAN_NOT_FOUND)
    LoanDAO.delete_payment(payment_id, loan_id)
    _refresh_status(loan_id, user_id)  # 合计不足本金 → 回到进行中
    audit_service.record(
        user_id,
        "loan.payment_delete",
        "loan",
        loan_id,
        "删除还款记录 #" + str(payment_id),
    )
    return list_payments(loan_id, user_id)
