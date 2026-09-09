"""账单流水接口"""
from typing import Optional

from fastapi import APIRouter, Query

from app.schemas.bill import BillCreate, BillOut, BillUpdate
from app.schemas.common import PageResult
from app.services import bill_service

router = APIRouter(prefix="/api/bill", tags=["流水管理"])


@router.get("/list", response_model=PageResult[BillOut], summary="分页查询账单流水")
def list_bills(
    start: Optional[str] = Query(None, description="起始时间，如 2024-01-01"),
    end: Optional[str] = Query(None, description="结束时间，如 2024-12-31"),
    account: Optional[str] = Query(None, description="账户类型：wechat/alipay"),
    tx_type: Optional[str] = Query(None, description="收支类型：expense/income/transfer"),
    category: Optional[str] = Query(None, description="消费分类"),
    sort_by: str = Query("tx_time", description="排序字段：tx_time/account/tx_type/merchant/amount/category/remark"),
    order: str = Query("desc", description="排序方向：asc/desc"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
):
    total, items = bill_service.list_bills(
        {
            "start": start,
            "end": end,
            "account": account,
            "tx_type": tx_type,
            "category": category,
        },
        page,
        page_size,
        sort_by=sort_by,
        order=order,
    )
    return PageResult(total=total, page=page, page_size=page_size, items=items)


@router.get("/{bill_id}", response_model=BillOut, summary="获取单条账单")
def get_bill(bill_id: int):
    return bill_service.get_bill(bill_id)


@router.post("", response_model=BillOut, status_code=201, summary="手动新增账单")
def create_bill(payload: BillCreate):
    return bill_service.create_bill(payload)


@router.put("/{bill_id}", response_model=BillOut, summary="编辑账单")
def update_bill(bill_id: int, payload: BillUpdate):
    return bill_service.update_bill(bill_id, payload)


@router.delete("/{bill_id}", status_code=204, summary="删除账单")
def delete_bill(bill_id: int):
    bill_service.delete_bill(bill_id)
