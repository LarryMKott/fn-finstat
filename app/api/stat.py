"""统计报表接口（仅统计当前飞牛账号的账单）"""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import GatewayUser, get_gateway_user
from app.schemas.stat import MerchantItem, MonthPoint, PieItem, StatSummary
from app.services import stat_service

router = APIRouter(prefix="/api/stat", tags=["统计报表"])


@router.get("/summary", response_model=StatSummary, summary="收支汇总统计（当前账号）")
def stat_summary(
    start: Optional[str] = Query(None, description="起始时间"),
    end: Optional[str] = Query(None, description="结束时间"),
    account: Optional[str] = Query(None, description="账户类型：wechat/alipay"),
    tx_type: Optional[str] = Query(None, description="收支类型：expense/income/transfer"),
    user: GatewayUser = Depends(get_gateway_user),
):
    return stat_service.summary(user.user_id, start, end, account, tx_type)


@router.get("/month_trend", response_model=list[MonthPoint], summary="月度收支趋势（当前账号）")
def stat_month_trend(
    start: Optional[str] = Query(None, description="起始时间"),
    end: Optional[str] = Query(None, description="结束时间"),
    account: Optional[str] = Query(None, description="账户类型"),
    tx_type: Optional[str] = Query(None, description="收支类型"),
    user: GatewayUser = Depends(get_gateway_user),
):
    return stat_service.month_trend(user.user_id, start, end, account, tx_type)


@router.get("/category_pie", response_model=list[PieItem], summary="分类支出饼图数据（当前账号）")
def stat_category_pie(
    start: Optional[str] = Query(None, description="起始时间"),
    end: Optional[str] = Query(None, description="结束时间"),
    account: Optional[str] = Query(None, description="账户类型"),
    user: GatewayUser = Depends(get_gateway_user),
):
    return stat_service.category_pie(user.user_id, start, end, account)


@router.get("/merchant_top", response_model=list[MerchantItem], summary="商户消费 TOP 排行（当前账号）")
def stat_merchant_top(
    start: Optional[str] = Query(None, description="起始时间"),
    end: Optional[str] = Query(None, description="结束时间"),
    account: Optional[str] = Query(None, description="账户类型"),
    limit: int = Query(10, ge=1, le=50, description="返回条数"),
    user: GatewayUser = Depends(get_gateway_user),
):
    return stat_service.merchant_top(user.user_id, start, end, account, limit)
