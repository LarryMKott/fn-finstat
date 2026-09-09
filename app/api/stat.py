"""统计报表接口"""
from typing import Optional

from fastapi import APIRouter, Query

from app.schemas.stat import MerchantItem, MonthPoint, PieItem, StatSummary
from app.services import stat_service

router = APIRouter(prefix="/api/stat", tags=["统计报表"])


@router.get("/summary", response_model=StatSummary, summary="收支汇总统计")
def stat_summary(
    start: Optional[str] = Query(None, description="起始时间"),
    end: Optional[str] = Query(None, description="结束时间"),
    account: Optional[str] = Query(None, description="账户类型：wechat/alipay"),
    tx_type: Optional[str] = Query(None, description="收支类型：expense/income/transfer"),
):
    return stat_service.summary(start, end, account, tx_type)


@router.get("/month_trend", response_model=list[MonthPoint], summary="月度收支趋势")
def stat_month_trend(
    start: Optional[str] = Query(None, description="起始时间"),
    end: Optional[str] = Query(None, description="结束时间"),
    account: Optional[str] = Query(None, description="账户类型"),
    tx_type: Optional[str] = Query(None, description="收支类型"),
):
    return stat_service.month_trend(start, end, account, tx_type)


@router.get("/category_pie", response_model=list[PieItem], summary="分类支出饼图数据")
def stat_category_pie(
    start: Optional[str] = Query(None, description="起始时间"),
    end: Optional[str] = Query(None, description="结束时间"),
    account: Optional[str] = Query(None, description="账户类型"),
):
    return stat_service.category_pie(start, end, account)


@router.get("/merchant_top", response_model=list[MerchantItem], summary="商户消费 TOP 排行")
def stat_merchant_top(
    start: Optional[str] = Query(None, description="起始时间"),
    end: Optional[str] = Query(None, description="结束时间"),
    account: Optional[str] = Query(None, description="账户类型"),
    limit: int = Query(10, ge=1, le=50, description="返回条数"),
):
    return stat_service.merchant_top(start, end, account, limit)
