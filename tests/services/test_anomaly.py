"""异常自检服务测试（AI-1）：四类异常判定 / 合并推送 / 数据不足不误报

判定口径为脑洞清单 §7.2 的用户决策（分类抬高 1.5×、单笔大额 3×、
周级推送、单周期合并为一条通知），测试同时钉住这些决策不被悄悄改掉。
"""

from datetime import date

from app.db.dao.bill_dao import BillDAO
from app.services import anomaly_service
from tests.conftest import USER_A, USER_B, make_bill_records

TODAY = date(2026, 9, 20)  # 月中：当月累计与固定项判定均有意义
_HISTORY_MONTHS = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]


def _seed_history(prefix: str, user_id: str = USER_A, monthly: float = 100):
    """近 6 个完整月：每月餐饮 monthly 元（月中记账）"""
    for i, m in enumerate(_HISTORY_MONTHS):
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"{prefix}-{i}",
                tx_time=f"{m}-15 10:00:00",
                tx_type="expense",
                amount=monthly,
                category="餐饮",
                merchant="反复餐厅",
            ),
            user_id,
        )


def test_category_spike_detected(db):
    """分类抬高：当月 200 vs 中位数 100，恰过 1.5× 阈值"""
    _seed_history("AN-H")
    BillDAO.insert_many(
        make_bill_records(
            1,
            prefix="AN-CUR",
            tx_time=f"{TODAY.year:04d}-{TODAY.month:02d}-10 10:00:00",
            tx_type="expense",
            amount=200,
            category="餐饮",
        ),
        USER_A,
    )
    lines = anomaly_service._check_user(USER_A, TODAY)
    assert any("分类「餐饮」" in line and "1.5 倍" not in line for line in lines)
    assert any("月中位数" in line for line in lines)


def test_outlier_bill_detected(db):
    """单笔大额：400 > 中位数 100 × 3（OUTLIER_FACTOR 复用预测口径）"""
    _seed_history("AN-O")
    BillDAO.insert_many(
        make_bill_records(
            1,
            prefix="AN-BIG",
            tx_time=f"{TODAY.year:04d}-{TODAY.month:02d}-10 10:00:00",
            tx_type="expense",
            amount=400,
            category="餐饮",
            merchant="一次消费",
        ),
        USER_A,
    )
    lines = anomaly_service._check_user(USER_A, TODAY)
    assert any("单笔大额" in line for line in lines)


def test_insufficient_history_no_false_positive(db):
    """历史不足 3 个有数据的月份不判定分类抬高（全是噪声）"""
    BillDAO.insert_many(
        make_bill_records(
            1,
            prefix="AN-ONE",
            tx_time="2026-08-15 10:00:00",
            tx_type="expense",
            amount=100,
            category="餐饮",
        ),
        USER_A,
    )
    BillDAO.insert_many(
        make_bill_records(
            1,
            prefix="AN-CUR2",
            tx_time=f"{TODAY.year:04d}-{TODAY.month:02d}-10 10:00:00",
            tx_type="expense",
            amount=500,
            category="餐饮",
        ),
        USER_A,
    )
    lines = anomaly_service._check_user(USER_A, TODAY)
    assert not any("月中位数" in line for line in lines)


def test_weekly_check_merges_into_one_notification(db, monkeypatch):
    """周级合并：单账号多条异常合并为一条通知；多账号各自收到各自的通知"""
    _seed_history("AN-M-A")
    BillDAO.insert_many(
        make_bill_records(
            2,
            prefix="AN-MA",
            tx_time=f"{TODAY.year:04d}-{TODAY.month:02d}-10 10:00:00",
            tx_type="expense",
            amount=350,
            category="餐饮",
        ),
        USER_A,
    )
    # B 账号独立数据：历史 6 个月每月 50，当月 300 → 自己的抬高异常
    _seed_history("AN-M-B", user_id=USER_B, monthly=50)
    BillDAO.insert_many(
        make_bill_records(
            1,
            prefix="AN-MB",
            tx_time=f"{TODAY.year:04d}-{TODAY.month:02d}-11 10:00:00",
            tx_type="expense",
            amount=300,
            category="餐饮",
        ),
        USER_B,
    )

    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        anomaly_service.notify_service,
        "notify_anomaly",
        lambda user_id, lines, today=None: calls.append((user_id, lines)),
    )
    total, message = anomaly_service.weekly_check(TODAY)
    assert total >= 2 and "推送 2 人" in message
    by_user = {u: lines for u, lines in calls}
    assert set(by_user) == {USER_A, USER_B}
    # 每账号一条合并通知（列表即合并结果，不是逐条推送多次调用）
    assert len([u for u, _ in calls if u == USER_A]) == 1
