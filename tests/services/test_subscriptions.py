"""订阅侦探测试（AI-5）：时间线聚合 / 台阶涨价 / 疑似僵尸订阅 / 占收入比

口径钉住脑洞清单 AI-5 的设计：比固定项识别宽松——窗口内出现 ≥3 个月即按
订阅分析（涨价月天然破坏带宽、断缴一个月不该整项消失）；
涨价 = 月费跳升 ≥1.2 且此后每月不再回落；僵尸 = 整窗每月出现且波动 ≤25%。
"""

from datetime import date

from app.db.dao.bill_dao import BillDAO
from app.services import forecast_service
from app.utils.period import last_full_months
from tests.conftest import USER_A, USER_B, make_bill_records

TODAY = date(2026, 9, 28)
WINDOW = last_full_months(TODAY, 12)  # 2025-09 ~ 2026-08，升序


def _seed(merchant: str, monthly: dict[str, float], prefix: str, **overrides) -> None:
    """按月造某商户的流水；prefix 须按月可区分（tx_id 全局唯一去重）"""
    for m, amount in monthly.items():
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"{prefix}-{m}",
                tx_time=f"{m}-05 10:00:00",
                tx_type="expense",
                amount=amount,
                merchant=merchant,
                category="会员订阅",
                **overrides,
            ),
            USER_A,
        )


def _range_map(values: list[float]) -> dict[str, float]:
    return dict(zip(WINDOW, values))


def test_full_year_stable_subscription_is_zombie(db):
    """整窗 12 个月稳定扣费：时间线完整、连续 12 个月、判为疑似僵尸"""
    _seed("视频会员", _range_map([15.0] * 12), "SUB-Z")
    data = forecast_service.subscriptions(USER_A, today=TODAY)
    assert [s["merchant"] for s in data["subscriptions"]] == ["视频会员"]
    s = data["subscriptions"][0]
    assert s["monthly_amount"] == 15.0
    assert s["months_hit"] == 12 and s["streak_months"] == 12
    assert s["first_month"] == WINDOW[0]
    assert s["category"] == "会员订阅"
    assert s["zombie"] is True and s["price_step"] is None
    assert data["monthly_total"] == 15.0
    assert data["flag_counts"] == {"price_step": 0, "zombie": 1}


def test_few_occurrences_skipped(db):
    """窗口内出现 < 3 个月的商户不按订阅分析（更像一次性消费）"""
    _seed("偶尔外卖", {WINDOW[0]: 30.0, WINDOW[3]: 30.0}, "SUB-FEW")
    data = forecast_service.subscriptions(USER_A, today=TODAY)
    assert data["subscriptions"] == []
    # 无商户名的流水不参与订阅分析
    BillDAO.insert_many(
        make_bill_records(
            3,
            prefix="SUB-NOMERCHANT",
            tx_time=f"{WINDOW[0]}-05 10:00:00",
            tx_type="expense",
            merchant="",
        ),
        USER_A,
    )
    assert forecast_service.subscriptions(USER_A, today=TODAY)["subscriptions"] == []


def test_price_step_detected_and_not_zombie(db):
    """台阶涨价：前 8 个月 10 元、后 4 个月 20 元 → 起始月/前后水平命中，非僵尸"""
    _seed(
        "网费",
        _range_map([10.0] * 8 + [20.0] * 4),
        "SUB-STEP",
    )
    s = forecast_service.subscriptions(USER_A, today=TODAY)["subscriptions"][0]
    assert s["price_step"] == {
        "since": WINDOW[8],
        "from": 10.0,
        "to": 20.0,
        "pct": 100.0,
    }
    assert s["zombie"] is False
    assert s["streak_months"] == 12 and s["months_hit"] == 12
    assert s["first_month"] == WINDOW[0]


def test_single_month_spike_not_flagged(db):
    """单月尖峰（年费/退款冲正）不误报涨价：台阶必须被后续月份保持"""
    values = [10.0] * 12
    values[5] = 200.0
    _seed("云存储", _range_map(values), "SUB-SPIKE")
    s = forecast_service.subscriptions(USER_A, today=TODAY)["subscriptions"][0]
    assert s["price_step"] is None
    assert s["monthly_amount"] == 10.0  # 中位数口径不受尖峰抬高
    assert s["zombie"] is False  # 波动远超 25% 带宽


def test_gap_breaks_streak_but_stays_listed(db):
    """断缴几个月不整项消失：连续月数从最近完整月往前数"""
    values = [10.0] * 3 + [0.0] * 3 + [10.0] * 6
    _seed("健身会员", _range_map(values), "SUB-GAP")
    s = forecast_service.subscriptions(USER_A, today=TODAY)["subscriptions"][0]
    assert s["months_hit"] == 9 and s["streak_months"] == 6
    assert s["zombie"] is False


def test_monthly_total_and_income_share(db):
    """月合计为各订阅月均之和；占收入比按非零月收入中位数计"""
    _seed("视频会员", _range_map([15.0] * 12), "SUB-A")
    _seed("音乐会员", _range_map([5.0] * 12), "SUB-B")
    for m in WINDOW:
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"SUB-INC-{m}",
                tx_time=f"{m}-10 10:00:00",
                tx_type="income",
                amount=2000.0,
                merchant="工资",
            ),
            USER_A,
        )
    data = forecast_service.subscriptions(USER_A, today=TODAY)
    assert data["monthly_total"] == 20.0
    assert data["income_monthly"] == 2000.0
    assert data["income_pct"] == 1.0

    # 无收入流水的账号占收入比如实留空，不虚构
    data2 = forecast_service.subscriptions(USER_B, today=TODAY)
    assert data2["monthly_total"] == 0.0
    assert data2["income_pct"] is None
