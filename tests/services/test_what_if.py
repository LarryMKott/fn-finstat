"""What-if 反事实模拟测试（AI-9）：基线聚合 / 情景外推 / 校验 / 储蓄目标联动

口径钉住 devlog 设计：分类基线月均 = 近 6 个完整月合计 ÷ 月数；情景差额可正可负
（砍为 0 或加码）；目标可行性 = 调整后月结余 vs 目标所需月均（savings_service
进度口径），未设目标日 / 目标日已过时 on_track 如实返回 None 不编结论。
"""

from datetime import date

import pytest

from app.core.errors import ValidationError
from app.db.dao.bill_dao import BillDAO
from app.db.dao.savings_dao import SavingsGoalDAO
from app.services import forecast_service
from app.utils.period import last_full_months
from tests.conftest import USER_A, USER_B, make_bill_records

TODAY = date(2026, 9, 29)
WINDOW = last_full_months(TODAY, forecast_service.WHAT_IF_WINDOW_MONTHS)


def _seed_monthly(
    merchant: str, category: str, monthly: float, user=USER_A, tx_type="expense"
):
    for m in WINDOW:
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"WIF-{user[:4]}-{merchant}-{m}",
                tx_time=f"{m}-05 10:00:00",
                tx_type=tx_type,
                amount=monthly,
                merchant=merchant,
                category=category,
            ),
            user,
        )


def test_baseline_aggregation(db):
    """基线 = 窗口合计 ÷ 月数：分类月均降序、月结余 = 月均收入 − 月均支出"""
    _seed_monthly("外卖平台", "餐饮", 1500)
    _seed_monthly("电商", "购物", 500)
    _seed_monthly("工资", "收入", 20000, tx_type="income")

    data = forecast_service.what_if(USER_A, today=TODAY)
    assert data["window"]["months"] == 6
    cats = {c["category"]: c["monthly_amount"] for c in data["baseline"]["categories"]}
    assert cats == {"餐饮": 1500.0, "购物": 500.0}
    assert data["baseline"]["monthly_income"] == 20000.0
    assert data["baseline"]["monthly_expense"] == 2000.0
    assert data["baseline"]["monthly_savings"] == 18000.0
    assert data["scenario"] is None  # 未提交调整，仅返回基线
    assert data["goal"] is None  # 无储蓄目标


def test_reduction_and_boost_scenario(db):
    """砍到目标值 → 差额为正、累计 = 差额 × 月数；调高于基线 → 差额为负"""
    _seed_monthly("外卖平台", "餐饮", 1500)
    data = forecast_service.what_if(
        USER_A,
        adjustments=[{"category": "餐饮", "monthly_amount": 800}],
        months=12,
        today=TODAY,
    )
    s = data["scenario"]
    assert s["items"] == [
        {
            "category": "餐饮",
            "baseline_monthly": 1500.0,
            "target_monthly": 800.0,
            "delta_monthly": 700.0,
        }
    ]
    assert s["delta_monthly"] == 700.0
    assert s["cumulative_delta"] == 8400.0
    assert s["monthly_savings_after"] == -800.0  # 基线结余 −1500（只有支出）+ 700

    boost = forecast_service.what_if(
        USER_A,
        adjustments=[{"category": "餐饮", "monthly_amount": 2000}],
        months=6,
        today=TODAY,
    )["scenario"]
    assert boost["delta_monthly"] == -500.0
    assert boost["cumulative_delta"] == -3000.0
    assert boost["monthly_savings_after"] == -2000.0


def test_months_clamped(db):
    """模拟月数钳到 1~36，累计按钳后的月数计"""
    _seed_monthly("外卖平台", "餐饮", 1500)
    data = forecast_service.what_if(
        USER_A,
        adjustments=[{"category": "餐饮", "monthly_amount": 0}],
        months=99,
        today=TODAY,
    )
    assert data["caliber"]["months"] == 36
    assert data["scenario"]["months"] == 36
    assert data["scenario"]["cumulative_delta"] == 1500.0 * 36


def test_adjustment_validation(db):
    """未知分类 / 负数金额 / 重复分类 / 超数量，全部按参数不合法拒绝"""
    _seed_monthly("外卖平台", "餐饮", 1500)
    with pytest.raises(ValidationError):
        forecast_service.what_if(
            USER_A,
            adjustments=[{"category": "不存在", "monthly_amount": 100}],
            today=TODAY,
        )
    with pytest.raises(ValidationError):
        forecast_service.what_if(
            USER_A,
            adjustments=[{"category": "餐饮", "monthly_amount": -1}],
            today=TODAY,
        )
    dup = [
        {"category": "餐饮", "monthly_amount": 100},
        {"category": "餐饮", "monthly_amount": 200},
    ]
    with pytest.raises(ValidationError):
        forecast_service.what_if(USER_A, adjustments=dup, today=TODAY)
    many = [
        {"category": f"分类{i}", "monthly_amount": 10}
        for i in range(forecast_service.WHAT_IF_MAX_SCOPES + 1)
    ]
    with pytest.raises(ValidationError):
        forecast_service.what_if(USER_A, adjustments=many, today=TODAY)


def test_baseline_top_cap_and_user_isolation(db):
    """基线只返回月均前 8 类；账号之间互不可见"""
    for i in range(10):
        _seed_monthly(f"商户{i}", f"分类{i}", 1000 - i * 10)
    data = forecast_service.what_if(USER_A, today=TODAY)
    assert len(data["baseline"]["categories"]) == forecast_service.WHAT_IF_BASELINE_TOP
    amounts = [c["monthly_amount"] for c in data["baseline"]["categories"]]
    assert amounts == sorted(amounts, reverse=True)

    other = forecast_service.what_if(USER_B, today=TODAY)
    assert other["baseline"]["categories"] == []
    assert other["baseline"]["monthly_savings"] == 0.0


def test_goal_linkage_on_track(db):
    """目标联动：调整后月结余 ≥ 所需月均 → 可按时达成；不足时给差额"""
    _seed_monthly("工资", "收入", 20000, tx_type="income")
    _seed_monthly("外卖平台", "餐饮", 1500)
    _seed_monthly("电商", "购物", 500)
    SavingsGoalDAO.create(
        USER_A,
        {
            "name": "换电脑",
            "target_amount": 37000.0,
            "start_date": TODAY.isoformat(),
            "target_date": "2026-11-30",  # 62 天 ≈ 2.0 个月 → 需 18500 元/月
            "note": "",
        },
    )
    # 基线结余 18000 < 所需：仅基线不编结论；餐饮砍到 800（+700）→ 18700 ≥ 所需 → 达成
    # 所需月均 = 37000 ÷ (62 天 ÷ 30.44) = 18165.81（_progress 的月均长度不取整）
    base = forecast_service.what_if(USER_A, today=TODAY)
    assert base["goal"]["per_month_needed"] == 18165.81
    assert base["goal"]["on_track"] is None  # 无情景不编结论

    hit = forecast_service.what_if(
        USER_A, adjustments=[{"category": "餐饮", "monthly_amount": 800}], today=TODAY
    )
    assert hit["goal"]["monthly_savings_after"] == 18700.0
    assert hit["goal"]["on_track"] is True
    assert "shortfall" not in hit["goal"]

    miss = forecast_service.what_if(
        USER_A, adjustments=[{"category": "餐饮", "monthly_amount": 1400}], today=TODAY
    )
    assert miss["goal"]["on_track"] is False
    assert miss["goal"]["shortfall"] == 65.81  # 18165.81 − (18000 + 100)


def test_goal_linkage_without_deadline_or_overdue(db):
    """未设目标日 / 目标日已过：没有可靠「所需月均」口径，on_track 如实留空"""
    _seed_monthly("外卖平台", "餐饮", 1500)
    SavingsGoalDAO.create(
        USER_A,
        {
            "name": "无期限",
            "target_amount": 5000.0,
            "start_date": TODAY.isoformat(),
            "target_date": None,
            "note": "",
        },
    )
    data = forecast_service.what_if(
        USER_A, adjustments=[{"category": "餐饮", "monthly_amount": 0}], today=TODAY
    )
    assert data["goal"]["on_track"] is None
    assert data["goal"]["per_month_needed"] is None

    SavingsGoalDAO.create(
        USER_A,
        {
            "name": "已过期",
            "target_amount": 5000.0,
            "start_date": "2026-01-01",
            "target_date": "2026-02-01",
            "note": "",
        },
    )
    overdue = forecast_service.what_if(
        USER_A, adjustments=[{"category": "餐饮", "monthly_amount": 0}], today=TODAY
    )
    # 过期目标在 with_deadline 里排最前，但目标日已过 → on_track=None
    assert overdue["goal"]["name"] == "已过期"
    assert overdue["goal"]["on_track"] is None
