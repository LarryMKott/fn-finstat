"""财务健康评分测试（T-1.3）：三分项口径、缺数据归一、公式公开

口径基线（与实现一致，公式在响应 items[].formula 完全公开）：
- 储蓄率 =（月均收入 − 月均支出）÷ 月均收入（近 6 个完整月）；≥20% 满分
- 负债率 = 最新快照负债 ÷（资产 + 负债）；≤30% 满分、≥70% 零分
- 应急金月数 = 最新快照资产 ÷ 月均支出；≥6 个月满分
"""

from datetime import date

from app.db.dao.asset_dao import AssetDAO
from app.db.dao.bill_dao import BillDAO
from app.services import stat_service
from tests.conftest import USER_A, make_bill_records


def _seed_month(base_tx, month, income=None, expense=None):
    if income is not None:
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"{base_tx}-I",
                tx_time=f"{month}-10 10:00:00",
                tx_type="income",
                amount=income,
                category="其他",
            ),
            USER_A,
        )
    if expense is not None:
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"{base_tx}-E",
                tx_time=f"{month}-20 10:00:00",
                tx_type="expense",
                amount=expense,
                category="餐饮",
            ),
            USER_A,
        )


def test_health_score_with_full_data(db):
    """近 6 个月收入 100 / 支出 40：储蓄率 60%（满分），快照负债 30%：满分"""
    today = date(2026, 9, 20)
    months = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
    for i, m in enumerate(months):
        _seed_month(f"HS{i}", m, income=100, expense=40)
    # 最新快照：资产 700 / 负债 300 → 负债率 30%（满分）；应急金 700/40 = 17.5 个月（满分）
    AssetDAO.create(
        {
            "snap_date": "2026-09-19",
            "name": "存款",
            "asset_type": "asset",
            "amount": 700,
        },
        USER_A,
    )
    AssetDAO.create(
        {
            "snap_date": "2026-09-19",
            "name": "房贷",
            "asset_type": "liability",
            "amount": 300,
        },
        USER_A,
    )

    report = stat_service.health_score(USER_A, today=today)
    assert report["window"]["months"] == 6
    assert report["avg_income"] == 100
    assert report["avg_expense"] == 40
    by_key = {i["key"]: i for i in report["items"]}
    assert by_key["savings"]["value"] == 60.0 and by_key["savings"]["score"] == 100
    assert by_key["debt"]["value"] == 30.0 and by_key["debt"]["score"] == 100
    assert by_key["emergency"]["value"] == 17.5 and by_key["emergency"]["score"] == 100
    assert report["score"] == 100 and report["grade"] == "优秀"
    # 口径完全公开：每个分项带公式
    assert all(len(i["formula"]) > 10 for i in report["items"])


def test_health_score_partial_and_renormalization(db):
    """储蓄率低且无快照：缺数据分项不计分，总分按剩余权重归一；响应提示缺因"""
    today = date(2026, 9, 20)
    months = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
    for i, m in enumerate(months):
        _seed_month(f"HP{i}", m, income=100, expense=90)  # 储蓄率 10% → 50 分

    report = stat_service.health_score(USER_A, today=today)
    by_key = {i["key"]: i for i in report["items"]}
    assert by_key["savings"]["score"] == 50
    assert by_key["debt"]["available"] is False and by_key["debt"]["hint"]
    assert by_key["emergency"]["available"] is False
    # 只有储蓄率计分：总分 = 50 × 1（权重归一）
    assert report["score"] == 50
    assert report["grade"] == "一般"


def test_health_score_zero_income_is_not_scored(db):
    """无收入记录：储蓄率不可评（除零保护），负债/应急若有快照仍可评"""
    today = date(2026, 9, 20)
    months = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
    for i, m in enumerate(months):
        _seed_month(f"HZ{i}", m, expense=50)  # 只有支出
    AssetDAO.create(
        {
            "snap_date": "2026-09-19",
            "name": "存款",
            "asset_type": "asset",
            "amount": 600,
        },
        USER_A,
    )

    report = stat_service.health_score(USER_A, today=today)
    by_key = {i["key"]: i for i in report["items"]}
    assert by_key["savings"]["available"] is False
    assert by_key["emergency"]["value"] == 12.0  # 600 / 50
    assert by_key["emergency"]["score"] == 100
    # 权重归一：0.3 / 0.6 = 应急金单分项
    assert report["score"] == 100
