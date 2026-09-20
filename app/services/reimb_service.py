"""报销 / 垫付工作流业务逻辑（T-7.4，按当前飞牛账号隔离）

报销单 = 一组支出的报销进度跟踪：状态机 待提交 → 已提交 → 部分到账 → 已结清，
到账金额 / 日期记录在报销单上。 bills.reimbursed 布尔由本层随关联同步
（挂单置 1、摘除置 0），既有「报销筛选 / 导出列」零破坏。

口径约定：报销支出仍按普通支出计入统计（报销是资金回收，不冲减消费），
报销单只跟踪回收进度，不改任何统计结果。
"""

from app.core.constants import REIMBURSEMENT_STATUSES
from app.core.errors import ErrorCode, NotFoundError, ValidationError
from app.db.dao.bill_dao import BillDAO
from app.db.dao.reimb_dao import ReimbDAO
from app.services import audit_service
from app.utils.amount import round2
from app.utils.period import valid_date

TITLE_MAX = 64
NOTE_MAX = 255

# 需要登记到账金额的状态：部分到账 / 已结清
_AMOUNT_REQUIRED_STATUSES = ("partial", "settled")


def _require_claim(claim_id: int, user_id: str) -> dict:
    claim = ReimbDAO.get(claim_id, user_id)
    if claim is None:
        raise NotFoundError("报销单不存在", code=ErrorCode.REIM_NOT_FOUND)
    return claim


def _clean_title(title: str) -> str:
    cleaned = (title or "").strip()
    if not cleaned:
        raise ValidationError("报销单名称不能为空", code=ErrorCode.REIM_INVALID)
    if len(cleaned) > TITLE_MAX:
        raise ValidationError(
            f"报销单名称不能超过 {TITLE_MAX} 个字符", code=ErrorCode.REIM_INVALID
        )
    return cleaned


def _validate_received(status: str, received_amount, received_date) -> tuple:
    """到账信息与状态的一致性：部分到账 / 已结清必须登记到账金额"""
    if status not in REIMBURSEMENT_STATUSES:
        raise ValidationError("无效的报销状态", code=ErrorCode.REIM_INVALID)
    if status in _AMOUNT_REQUIRED_STATUSES:
        if received_amount is None:
            raise ValidationError("该状态需要登记到账金额", code=ErrorCode.REIM_INVALID)
        amount = round(float(received_amount), 2)
        if amount < 0:
            raise ValidationError("到账金额不能为负数", code=ErrorCode.REIM_INVALID)
    else:
        amount = None
    if received_date is not None:
        # 真实解析校验（长度检查放过 2026-13-45 这类垃圾串）
        received_date = valid_date(received_date, "到账日期")
    return amount, received_date


def list_claims(user_id: str) -> list[dict]:
    """报销单列表（含关联流水笔数与金额合计），按创建时间倒序"""
    claims = ReimbDAO.list_with_stats(user_id)
    for c in claims:
        c["status_label"] = _status_label(c["status"])
    return claims


def _status_label(status: str) -> str:
    from app.core.constants import REIMBURSEMENT_STATUS_LABELS

    return REIMBURSEMENT_STATUS_LABELS.get(status, status)


def create_claim(payload, user_id: str) -> dict:
    """新建报销单（固定待提交状态）"""
    title = _clean_title(payload.title)
    note = (payload.note or "").strip()[:NOTE_MAX]
    claim = ReimbDAO.create(user_id, title, note)
    audit_service.record(
        user_id,
        "reimb.create",
        "reimbursement",
        claim["id"],
        "新建报销单「" + title + "」",
    )
    claim["status_label"] = _status_label(claim["status"])
    return claim


def update_claim(claim_id: int, payload, user_id: str) -> dict:
    """更新报销单：名称 / 备注 / 状态流转 / 到账登记

    到账信息与状态联动校验：部分到账、已结清必须登记到账金额；回到
    待提交 / 已提交时清空到账登记。
    """
    _require_claim(claim_id, user_id)
    fields: dict = {}
    if payload.title is not None:
        fields["title"] = _clean_title(payload.title)
    if payload.note is not None:
        fields["note"] = payload.note.strip()[:NOTE_MAX]
    if payload.status is not None:
        if payload.status not in REIMBURSEMENT_STATUSES:
            raise ValidationError("无效的报销状态", code=ErrorCode.REIM_INVALID)
        amount, received_date = _validate_received(
            payload.status, payload.received_amount, payload.received_date
        )
        fields["status"] = payload.status
        fields["received_amount"] = amount
        fields["received_date"] = received_date
    else:
        # 区分「未传」与「显式传 null」：文档约定 received_date: null = 清空到账
        # 日期，靠 model_fields_set 判断（is not None 分不清两者）
        provided = getattr(payload, "model_fields_set", set()) or set()
        if provided & {"received_amount", "received_date"}:
            # 不改状态、只登记到账信息：沿用当前状态做一致性校验
            current = ReimbDAO.get(claim_id, user_id)
            status = payload.status or current["status"]
            amount, received_date = _validate_received(
                status,
                (
                    payload.received_amount
                    if "received_amount" in provided
                    else current["received_amount"]
                ),
                (
                    payload.received_date
                    if "received_date" in provided
                    else current["received_date"]
                ),
            )
            fields["received_amount"] = amount
            fields["received_date"] = received_date
    updated = ReimbDAO.update_fields(claim_id, user_id, fields)
    if updated is None:
        raise NotFoundError("报销单不存在", code=ErrorCode.REIM_NOT_FOUND)
    summary = (
        "报销单「" + updated["title"] + "」状态 → " + _status_label(updated["status"])
    )
    if updated["received_amount"] is not None:
        summary += "（到账 " + str(updated["received_amount"]) + " 元"
        if updated["received_date"]:
            summary += "，" + updated["received_date"]
        summary += "）"
    audit_service.record(user_id, "reimb.update", "reimbursement", claim_id, summary)
    updated["status_label"] = _status_label(updated["status"])
    return updated


def delete_claim(claim_id: int, user_id: str) -> None:
    """删除报销单并摘除其下流水（报销标记复位），不存在抛 404"""
    if not ReimbDAO.delete(claim_id, user_id):
        raise NotFoundError("报销单不存在", code=ErrorCode.REIM_NOT_FOUND)
    audit_service.record(
        user_id, "reimb.delete", "reimbursement", claim_id, "删除报销单（流水已摘除）"
    )


def attach_bills(claim_id: int, ids: list[int], user_id: str) -> dict:
    """把支出流水挂到报销单：校验归属 / 支出类型 / 未挂其他报销单"""
    _require_claim(claim_id, user_id)
    unique_ids = list(dict.fromkeys(ids))
    if not unique_ids:
        raise ValidationError("请先勾选要加入的流水", code=ErrorCode.REIM_INVALID)
    bills = {b["id"]: b for b in BillDAO.list_by_ids(unique_ids, user_id)}
    for bill_id in unique_ids:
        bill = bills.get(bill_id)
        if bill is None:
            raise ValidationError(
                f"流水 {bill_id} 不存在或不属于当前账号",
                code=ErrorCode.REIM_INVALID,
            )
        if bill["deleted"]:
            raise ValidationError(
                f"流水 {bill_id} 已在回收站，不能加入报销单",
                code=ErrorCode.REIM_INVALID,
            )
        if bill["tx_type"] != "expense":
            raise ValidationError(
                "只有支出流水可以加入报销单", code=ErrorCode.REIM_INVALID
            )
        if bill["reimb_id"] is not None:
            raise ValidationError(
                f"流水 {bill_id} 已在其他报销单中，请先移除",
                code=ErrorCode.REIM_INVALID,
            )
    changed = ReimbDAO.attach_bills(claim_id, user_id, unique_ids)
    if changed != len(unique_ids):
        # 校验与写入分属两个事务，DAO 的 UPDATE 带业务守卫（未挂他单/未删/
        # 支出类型），条数不符即并发冲突——显式失败而不是静默少挂
        raise ValidationError(
            "部分流水已发生变化（可能已被挂单、删除或类型调整），请刷新后重试",
            code=ErrorCode.REIM_INVALID,
        )
    claim = ReimbDAO.get(claim_id, user_id)
    audit_service.record(
        user_id,
        "reimb.attach",
        "reimbursement",
        claim_id,
        str(changed) + " 条流水挂入报销单「" + (claim or {}).get("title", "?") + "」",
    )
    return {"claim_id": claim_id, "attached": changed}


def detach_bills(claim_id: int, ids: list[int], user_id: str) -> dict:
    """从报销单摘除流水（仅限本单内流水），报销标记复位"""
    _require_claim(claim_id, user_id)
    unique_ids = list(dict.fromkeys(ids))
    changed = ReimbDAO.detach_bills(claim_id, user_id, unique_ids)
    audit_service.record(
        user_id,
        "reimb.detach",
        "reimbursement",
        claim_id,
        str(changed) + " 条流水移出报销单",
    )
    return {"claim_id": claim_id, "detached": changed}


def list_claim_bills(claim_id: int, user_id: str) -> dict:
    """报销单内的流水明细（未删除，按交易时间倒序）"""
    _require_claim(claim_id, user_id)
    rows = ReimbDAO.list_bills(claim_id, user_id)
    total = round2(sum(float(r["amount"]) for r in rows))
    return {
        "claim_id": claim_id,
        "total": total,
        "count": len(rows),
        "rows": [
            {
                "id": r["id"],
                "tx_time": r["tx_time"],
                "merchant": r["merchant"],
                "category": r["category"],
                "amount": r["amount"],
                "reimbursed": r["reimbursed"],
            }
            for r in rows
        ],
    }
