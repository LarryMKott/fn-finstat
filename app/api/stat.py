"""统计报表接口（仅统计当前飞牛账号的账单，T-7.1 起支持账本维度）"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, request_db_session
from app.api.params import (
    AccountQuery,
    EndQuery,
    LedgerIdQuery,
    StartQuery,
    TxTypeQuery,
)
from app.schemas.common import ApiResponse, ok
from app.schemas.stat import (
    DailyPoint,
    HealthReport,
    MerchantItem,
    MonthPoint,
    PieItem,
    RegionMap,
    StatSummary,
    YearComparison,
)
from app.services import stat_service

router = APIRouter(
    prefix="/api/stat",
    tags=["统计报表"],
    dependencies=[Depends(request_db_session)],
)


@router.get(
    "/summary",
    response_model=ApiResponse[StatSummary],
    summary="收支汇总统计（当前账号）",
)
def stat_summary(
    user: CurrentUser,
    start: StartQuery = None,
    end: EndQuery = None,
    account: AccountQuery = None,
    tx_type: TxTypeQuery = None,
    ledger_id: LedgerIdQuery = None,
):
    return ok(
        stat_service.summary(user.user_id, start, end, account, tx_type, ledger_id)
    )


@router.get(
    "/month_trend",
    response_model=ApiResponse[list[MonthPoint]],
    summary="月度收支趋势（当前账号）",
)
def stat_month_trend(
    user: CurrentUser,
    start: StartQuery = None,
    end: EndQuery = None,
    account: AccountQuery = None,
    tx_type: TxTypeQuery = None,
    ledger_id: LedgerIdQuery = None,
):
    return ok(
        stat_service.month_trend(user.user_id, start, end, account, tx_type, ledger_id)
    )


@router.get(
    "/category_pie",
    response_model=ApiResponse[list[PieItem]],
    summary="分类支出饼图数据（当前账号）",
)
def stat_category_pie(
    user: CurrentUser,
    start: StartQuery = None,
    end: EndQuery = None,
    account: AccountQuery = None,
    ledger_id: LedgerIdQuery = None,
):
    return ok(stat_service.category_pie(user.user_id, start, end, account, ledger_id))


@router.get(
    "/merchant_top",
    response_model=ApiResponse[list[MerchantItem]],
    summary="商户消费 TOP 排行（当前账号）",
)
def stat_merchant_top(
    user: CurrentUser,
    start: StartQuery = None,
    end: EndQuery = None,
    account: AccountQuery = None,
    limit: int = Query(10, ge=1, le=50, description="返回条数"),
    ledger_id: LedgerIdQuery = None,
):
    return ok(
        stat_service.merchant_top(user.user_id, start, end, account, limit, ledger_id)
    )


@router.get(
    "/daily_heatmap",
    response_model=ApiResponse[list[DailyPoint]],
    summary="按日收支汇总（日历热力图，当前账号）",
)
def stat_daily_heatmap(
    user: CurrentUser,
    year: int = Query(..., description="年份，如 2026"),
    month: Optional[int] = Query(
        None, ge=1, le=12, description="月份（可选，默认全年）"
    ),
    account: AccountQuery = None,
    ledger_id: LedgerIdQuery = None,
):
    return ok(stat_service.daily_heatmap(user.user_id, year, month, account, ledger_id))


@router.get(
    "/year_comparison",
    response_model=ApiResponse[YearComparison],
    summary="年度对比报表（本年 vs 去年，当前账号）",
)
def stat_year_comparison(
    user: CurrentUser,
    year: Optional[int] = Query(None, description="年份（默认今年）"),
    account: AccountQuery = None,
    ledger_id: LedgerIdQuery = None,
):
    target_year = year or date.today().year
    return ok(
        stat_service.year_comparison(user.user_id, target_year, account, ledger_id)
    )


@router.get(
    "/region_map",
    response_model=ApiResponse[RegionMap],
    summary="消费地图：按省级行政区聚合支出（当前账号）",
)
def stat_region_map(
    user: CurrentUser,
    start: StartQuery = None,
    end: EndQuery = None,
    account: AccountQuery = None,
    ledger_id: LedgerIdQuery = None,
):
    """地域由商户名/备注文本推断（账单本身不含地区字段），响应内含识别率"""
    return ok(stat_service.region_map(user.user_id, start, end, account, ledger_id))


@router.get(
    "/health",
    response_model=ApiResponse[HealthReport],
    summary="财务健康评分（储蓄率 / 负债率 / 应急金月数，口径随响应公开）",
)
def stat_health(
    user: CurrentUser,
    today: Optional[str] = Query(
        None, description="评估基准日 YYYY-MM-DD（默认今天，测试/回看用）"
    ),
):
    """只读计算，不落库；缺数据的分项不计分并在 items[].hint 说明"""
    from datetime import date as _date

    base = _date.fromisoformat(today) if today else None
    return ok(stat_service.health_score(user.user_id, today=base))
