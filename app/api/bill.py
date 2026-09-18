"""账单流水接口（数据按当前飞牛账号隔离）

统一响应：除文件下载外均返回 {"code", "msg", "data"} 包装（response_model=ApiResponse）。
"""

from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import CurrentUser, request_db_session
from app.schemas.bill import (
    BatchBillRequest,
    BatchBillResult,
    BillCreate,
    BillIdsRequest,
    BillOut,
    BillUpdate,
)
from app.schemas.common import ApiResponse, PageResult, ok
from app.services import bill_service
from app.services.bill_service import BillFilters
from app.utils.file_utils import content_disposition

router = APIRouter(
    prefix="/api/bill",
    tags=["流水管理"],
    dependencies=[Depends(request_db_session)],
)


def bill_filters(
    start: Optional[str] = Query(None, description="起始时间，如 2024-01-01"),
    end: Optional[str] = Query(None, description="结束时间，如 2024-12-31"),
    account: Optional[str] = Query(
        None, description="账户类型：wechat/alipay/jd/unionpay"
    ),
    tx_type: Optional[str] = Query(
        None, description="收支类型：expense/income/transfer"
    ),
    category: Optional[str] = Query(None, description="消费分类"),
    tag: Optional[str] = Query(None, description="标签精确匹配"),
    reimbursed: Optional[bool] = Query(None, description="报销标记筛选"),
    categories: Optional[List[str]] = Query(
        None, description="多分类筛选（IN 匹配，可重复传参）"
    ),
    merchants: Optional[List[str]] = Query(
        None, description="多商户关键词筛选（OR 子串匹配，可重复传参）"
    ),
    ledger_id: Optional[int] = Query(
        None, ge=1, description="账本 id（T-7.1）；不传 = 不按账本过滤"
    ),
) -> BillFilters:
    """流水筛选条件依赖：list 与 export 两个端点共用同一组查询参数

    多值参数与自然语言查询同语义（T-6.2「存为筛选」口径复现）；
    条目数与长度在此钳制，DAO 层另有绑定参数兜底。
    ledger_id 由 DAO 层强制注入（与 user_id 同策略），None 表示不按账本过滤。
    """

    def _clean(values: Optional[List[str]], limit: int) -> Optional[tuple]:
        cleaned = [v.strip()[:64] for v in (values or []) if v and v.strip()]
        return tuple(dict.fromkeys(cleaned))[:limit] or None

    return BillFilters(
        start=start,
        end=end,
        account=account,
        tx_type=tx_type,
        category=category,
        tag=tag,
        reimbursed=reimbursed,
        categories=_clean(categories, 10),
        merchants=_clean(merchants, 10),
        ledger_id=ledger_id,
    )


FiltersDep = Annotated[BillFilters, Depends(bill_filters)]


@router.get(
    "/list",
    response_model=ApiResponse[PageResult[BillOut]],
    summary="分页查询账单流水（当前账号）",
)
def list_bills(
    user: CurrentUser,
    filters: FiltersDep,
    sort_by: str = Query(
        "tx_time",
        description="排序字段：tx_time/account/tx_type/merchant/amount/category/remark",
    ),
    order: str = Query("desc", description="排序方向：asc/desc"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
):
    total, items = bill_service.list_bills(
        user.user_id,
        filters.as_dict(),
        page,
        page_size,
        sort_by=sort_by,
        order=order,
    )
    return ok(PageResult(total=total, page=page, page_size=page_size, items=items))


@router.get(
    "/export",
    summary="按筛选条件导出流水（xlsx/csv，不含回收站）",
    response_class=Response,
)
def export_bills(
    user: CurrentUser,
    filters: FiltersDep,
    format: str = Query("xlsx", description="导出格式：xlsx/csv"),
):
    filename, content, media_type = bill_service.export_bills(
        user.user_id,
        filters.as_dict(),
        fmt=format,
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.post(
    "/batch",
    response_model=ApiResponse[BatchBillResult],
    summary="批量操作（改分类/打标签/报销/删除）",
)
def batch_bills(user: CurrentUser, payload: BatchBillRequest):
    """多选流水后统一执行批量操作；delete 为移入回收站，彻底删除用 purge"""
    updated = bill_service.batch_action(payload, user.user_id)
    return ok(BatchBillResult(updated=updated))


@router.get(
    "/recycle",
    response_model=ApiResponse[PageResult[BillOut]],
    summary="回收站列表（当前账号）",
)
def list_recycle(
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    total, items = bill_service.list_recycle(user.user_id, page, page_size)
    return ok(PageResult(total=total, page=page, page_size=page_size, items=items))


@router.post(
    "/recycle/restore",
    response_model=ApiResponse[BatchBillResult],
    summary="从回收站还原流水",
)
def restore_bills(user: CurrentUser, payload: BillIdsRequest):
    """还原后流水重新出现在流水列表"""
    updated = bill_service.restore_bills(payload.ids, user.user_id)
    return ok(BatchBillResult(updated=updated))


@router.post(
    "/recycle/empty", response_model=ApiResponse[BatchBillResult], summary="清空回收站"
)
def empty_recycle(user: CurrentUser):
    updated = bill_service.empty_recycle(user.user_id)
    return ok(BatchBillResult(updated=updated))


@router.delete(
    "/recycle",
    response_model=ApiResponse[BatchBillResult],
    summary="彻底删除回收站流水",
)
def purge_bills(user: CurrentUser, payload: BillIdsRequest):
    """彻底删除（不可恢复）"""
    updated = bill_service.purge_bills(payload.ids, user.user_id)
    return ok(BatchBillResult(updated=updated))


@router.get(
    "/{bill_id}",
    response_model=ApiResponse[BillOut],
    summary="获取单条账单（当前账号）",
)
def get_bill(user: CurrentUser, bill_id: int):
    return ok(bill_service.get_bill(bill_id, user.user_id))


@router.post(
    "",
    response_model=ApiResponse[BillOut],
    status_code=201,
    summary="手动新增账单（归属当前账号）",
)
def create_bill(user: CurrentUser, payload: BillCreate):
    """账本维度（T-7.1）：不传 ledger_id 的旧请求仍落在默认账本上"""
    return ok(bill_service.create_bill(payload, user.user_id, payload.ledger_id))


@router.put(
    "/{bill_id}", response_model=ApiResponse[BillOut], summary="编辑账单（仅当前账号）"
)
def update_bill(user: CurrentUser, bill_id: int, payload: BillUpdate):
    return ok(bill_service.update_bill(bill_id, payload, user.user_id))


@router.delete("/{bill_id}", status_code=204, summary="删除账单（移入回收站）")
def delete_bill(user: CurrentUser, bill_id: int):
    bill_service.delete_bill(bill_id, user.user_id)
