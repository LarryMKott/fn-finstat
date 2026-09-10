"""账单流水业务逻辑"""
from typing import Optional

from fastapi import HTTPException

from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from app.schemas.bill import BillCreate, BillUpdate
from app.utils.amount import normalize_amount

VALID_TYPES = {"expense", "income", "transfer"}
VALID_ACCOUNTS = {"wechat", "alipay"}

# 允许被更新的字段白名单（防止 SQL 注入与越权字段）
_UPDATE_FIELDS = {"tx_time", "account", "tx_type", "merchant", "amount", "category", "tx_id", "remark"}

# 允许排序的字段白名单（ORDER BY 拼接前校验，防止 SQL 注入）
_SORT_FIELDS = {"tx_time", "account", "tx_type", "merchant", "amount", "category", "remark"}


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


def list_bills(filters: dict, page: int, page_size: int, sort_by: str = "tx_time", order: str = "desc") -> tuple[int, list[dict]]:
    if sort_by not in _SORT_FIELDS:
        raise HTTPException(status_code=400, detail="无效的排序字段")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="无效的排序方向")
    return BillDAO.list_bills(**filters, page=page, page_size=page_size, sort_by=sort_by, order=order)


def get_bill(bill_id: int) -> dict:
    bill = BillDAO.get_by_id(bill_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="账单不存在")
    return bill


def create_bill(data: BillCreate) -> dict:
    _validate(data.tx_type, data.account, data.amount)
    if data.tx_id and BillDAO.tx_id_exists(data.tx_id):
        raise HTTPException(status_code=400, detail="交易单号已存在")
    _ensure_category(data.category)
    payload = data.model_dump()
    payload["amount"] = normalize_amount(payload["amount"])
    bill_id = BillDAO.create(payload)
    bill = BillDAO.get_by_id(bill_id)
    if bill is None:
        raise HTTPException(status_code=500, detail="新增失败")
    return bill


def update_bill(bill_id: int, data: BillUpdate) -> dict:
    if BillDAO.get_by_id(bill_id) is None:
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
    if "category" in fields and fields["category"]:
        _ensure_category(fields["category"])
    if "tx_id" in fields and fields.get("tx_id") and BillDAO.tx_id_exists(fields["tx_id"], exclude_id=bill_id):
        raise HTTPException(status_code=400, detail="交易单号已存在")

    BillDAO.update(bill_id, fields)
    updated = BillDAO.get_by_id(bill_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="更新失败")
    return updated


def delete_bill(bill_id: int) -> None:
    if not BillDAO.delete(bill_id):
        raise HTTPException(status_code=404, detail="账单不存在")
