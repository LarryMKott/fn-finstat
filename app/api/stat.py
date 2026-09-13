"""统计报表接口（仅统计当前飞牛账号的账单）"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import GatewayUser, get_gateway_user
from app.schemas.common import ApiResponse, ok
from app.schemas.stat import (
    DailyPoint,
    MerchantItem,
    MonthPoint,
    PieItem,
    RegionMap,
    StatSummary,
    YearComparison,
)
from app.services import stat_service

router = APIRouter(prefix="/api/stat", tags=["统计报表"])


@router.get(
    "/summary",
    response_model=ApiResponse[StatSummary],
    summary="收支汇总统计（当前账号）",
)
def stat_summary(
    start: Optional[str] = Query(None, description="起始时间"),
    end: Optional[str] = Query(None, description="结束时间"),
    account: Optional[str] = Query(None, description="账户类型：wechat/alipay"),
    tx_type: Optional[str] = Query(
        None, description="收支类型：expense/income/transfer"
    ),
    user: GatewayUser = Depends(get_gateway_user),
):
    return ok(stat_service.summary(user.user_id, start, end, account, tx_type))


@router.get(
    "/month_trend",
    response_model=ApiResponse[list[MonthPoint]],
    summary="月度收支趋势（当前账号）",
)
def stat_month_trend(
    start: Optional[str] = Query(None, description="起始时间"),
    end: Optional[str] = Query(None, description="结束时间"),
    account: Optional[str] = Query(None, description="账户类型"),
    tx_type: Optional[str] = Query(None, description="收支类型"),
    user: GatewayUser = Depends(get_gateway_user),
):
    return ok(stat_service.month_trend(user.user_id, start, end, account, tx_type))


@router.get(
    "/category_pie",
    response_model=ApiResponse[list[PieItem]],
    summary="分类支出饼图数据（当前账号）",
)
def stat_category_pie(
    start: Optional[str] = Query(None, description="起始时间"),
    end: Optional[str] = Query(None, description="结束时间"),
    account: Optional[str] = Query(None, description="账户类型"),
    user: GatewayUser = Depends(get_gateway_user),
):
    return ok(stat_service.category_pie(user.user_id, start, end, account))


@router.get(
    "/merchant_top",
    response_model=ApiResponse[list[MerchantItem]],
    summary="商户消费 TOP 排行（当前账号）",
)
def stat_merchant_top(
    start: Optional[str] = Query(None, description="起始时间"),
    end: Optional[str] = Query(None, description="结束时间"),
    account: Optional[str] = Query(None, description="账户类型"),
    limit: int = Query(10, ge=1, le=50, description="返回条数"),
    user: GatewayUser = Depends(get_gateway_user),
):
    return ok(stat_service.merchant_top(user.user_id, start, end, account, limit))


@router.get(
    "/daily_heatmap",
    response_model=ApiResponse[list[DailyPoint]],
    summary="按日收支汇总（日历热力图，当前账号）",
)
def stat_daily_heatmap(
    year: int = Query(..., description="年份，如 2026"),
    month: Optional[int] = Query(
        None, ge=1, le=12, description="月份（可选，默认全年）"
    ),
    account: Optional[str] = Query(None, description="账户类型"),
    user: GatewayUser = Depends(get_gateway_user),
):
    return ok(stat_service.daily_heatmap(user.user_id, year, month, account))


@router.get(
    "/year_comparison",
    response_model=ApiResponse[YearComparison],
    summary="年度对比报表（本年 vs 去年，当前账号）",
)
def stat_year_comparison(
    year: Optional[int] = Query(None, description="年份（默认今年）"),
    account: Optional[str] = Query(None, description="账户类型"),
    user: GatewayUser = Depends(get_gateway_user),
):
    target_year = year or date.today().year
    return ok(stat_service.year_comparison(user.user_id, target_year, account))


@router.get(
    "/region_map",
    response_model=ApiResponse[RegionMap],
    summary="消费地图：按省级行政区聚合支出（当前账号）",
)
def stat_region_map(
    start: Optional[str] = Query(None, description="起始时间"),
    end: Optional[str] = Query(None, description="结束时间"),
    account: Optional[str] = Query(None, description="账户类型"),
    user: GatewayUser = Depends(get_gateway_user),
):
    """地域由商户名/备注文本推断（账单本身不含地区字段），响应内含识别率"""
    return ok(stat_service.region_map(user.user_id, start, end, account))
