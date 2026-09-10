"""账单流水业务逻辑（数据按当前飞牛账号隔离）"""
from typing import Optional

from fastapi import HTTPException

from app.config import DEFAULT_CATEGORY
from app.db.base import UniqueViolationError
from app.db.dao.bill_dao import BillDAO, SORTABLE_FIELDS
from app.db.dao.category_dao import CategoryDAO
from app.schemas.bill import BillCreate, BillUpdate
from app.utils.amount import normalize_amount

VALID_TYPES = {"expense", "income", "transfer"}
VALID_ACCOUNTS = {"wechat", "alipay"}

# 允许被更新的字段白名单（防止 SQL 注入与越权字段）
_UPDATE_FIELDS = {"tx_time", "account", "tx_type", "merchant", "amount", "category", "tx_id", "remark"}


def _validate(tx_type: str, account: str, amount: float) -> None:
    if tx_type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail="无效的收支类型")
    if account not in VALID_ACCOUNTS:
        raise HTTPException(status_code=400, detail="无效的账户类型")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="金额必须大于 0")


def _ensure_category(name: str) -> None:
    """分类不存在时自动创建，避免手工录入被拦截"""
    if name and not CategoryDAO.get_by_name(name):
        CategoryDAO.create(name)


def list_bills(
    user_id: str, filters: dict, page: int, page_size: int, sort_by: str = "tx_time", order: str = "desc"
) -> tuple[int, list[dict]]:
    # 排序白名单以 DAO 层 SORTABLE_FIELDS 为单一来源
    if sort_by not in SORTABLE_FIELDS:
        raise HTTPException(status_code=400, detail="无效的排序字段")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="无效的排序方向")
    return BillDAO.list_bills(
        user_id, **filters, page=page, page_size=page_size, sort_by=sort_by, order=order
    )


def get_bill(bill_id: int, user_id: str) -> dict:
    bill = BillDAO.get_by_id(bill_id, user_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="账单不存在")
    return bill


def create_bill(data: BillCreate, user_id: str) -> dict:
    _validate(data.tx_type, data.account, data.amount)
    if data.tx_id and BillDAO.tx_id_exists(data.tx_id):
        raise HTTPException(status_code=400, detail="交易单号已存在")
    payload = data.model_dump()
    # 空分类归一化为默认分类，避免出现不在 categories 表中的孤儿分类
    payload["category"] = (payload["category"] or "").strip() or DEFAULT_CATEGORY
    _ensure_category(payload["category"])
    payload["amount"] = normalize_amount(payload["amount"])
    try:
        bill_id = BillDAO.create(payload, user_id)
    except UniqueViolationError:  # 并发下同名交易号越过预检查，由唯一约束兜底
        raise HTTPException(status_code=400, detail="交易单号已存在")
    bill = BillDAO.get_by_id(bill_id, user_id)
    if bill is None:
        raise HTTPException(status_code=500, detail="新增失败")
    return bill


def update_bill(bill_id: int, data: BillUpdate, user_id: str) -> dict:
    if BillDAO.get_by_id(bill_id, user_id) is None:
        raise HTTPException(status_code=404, detail="账单不存在")

    raw = data.model_dump(exclude_unset=True)
    fields = {k: v for k, v in raw.items() if k in _UPDATE_FIELDS and v is not None}
    # 空交易号归一化为 None（UNIQUE 允许多个 NULL，空串全局只允许一条）
    if fields.get("tx_id") == "":
        fields["tx_id"] = None

    if "tx_type" in fields and fields["tx_type"] not in VALID_TYPES:
        raise HTTPException(status_code=400, detail="无效的收支类型")
    if "account" in fields and fields["account"] not in VALID_ACCOUNTS:
        raise HTTPException(status_code=400, detail="无效的账户类型")
    if "amount" in fields and fields["amount"] <= 0:
        raise HTTPException(status_code=400, detail="金额必须大于 0")
    if "amount" in fields:
        fields["amount"] = normalize_amount(fields["amount"])
    if "category" in fields:
        # 空分类归一化为默认分类，与新增逻辑一致
        fields["category"] = (fields["category"] or "").strip() or DEFAULT_CATEGORY
        _ensure_category(fields["category"])
    if "tx_id" in fields and fields.get("tx_id") and BillDAO.tx_id_exists(fields["tx_id"], exclude_id=bill_id):
        raise HTTPException(status_code=400, detail="交易单号已存在")

    try:
        BillDAO.update(bill_id, fields, user_id)
    except UniqueViolationError:  # 并发下同名交易号越过预检查，由唯一约束兜底
        raise HTTPException(status_code=400, detail="交易单号已存在")
    updated = BillDAO.get_by_id(bill_id, user_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="更新失败")
    return updated


def delete_bill(bill_id: int, user_id: str) -> None:
    if not BillDAO.delete(bill_id, user_id):
        raise HTTPException(status_code=404, detail="账单不存在")
