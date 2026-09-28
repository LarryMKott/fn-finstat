"""记账数据体检测试（AI-2）：六类体检项计数 / 跳转口径 / 网关与本地模式差异

口径钉住：未分类/空商户/零金额计数不含回收站；未记账月份自
max(首个记账月, 近 6 个月窗口起点) 逐月查缺；疑似重复 = 同日+同类型+
同商户+同金额 ≥2 条（退款对类型不同不成组、转账不参与）；
「无归属账号」仅在网关多账号模式（user_id 非空）下呈现。
"""

from datetime import date

from app.config import DEFAULT_CATEGORY
from app.db.dao.bill_dao import BillDAO
from app.services import data_check_service
from tests.conftest import USER_A, make_bill_records

TODAY = date(2026, 9, 20)  # 当前月 2026-09，近 6 个月窗口起点 2026-04


def _items(data: dict) -> dict:
    return {i["key"]: i for i in data["items"]}


def _seed(rows: list[dict], user_id: str = USER_A) -> None:
    BillDAO.insert_many(rows, user_id)


def test_clean_account_has_no_problems(db):
    data = data_check_service.data_check(USER_A, today=TODAY)
    assert data["problem_count"] == 0 and data["problem_total"] == 0
    assert len(data["items"]) == 5  # 网关模式下「无归属」为 0 时不出现
    for item in data["items"]:
        assert item["count"] == 0 and item["jump"] is None


def test_uncategorized_count_and_jump(db):
    _seed(
        make_bill_records(2, prefix="DC-UNC", category=DEFAULT_CATEGORY)
        + make_bill_records(1, prefix="DC-CAT", category="餐饮")
    )
    item = _items(data_check_service.data_check(USER_A, today=TODAY))["uncategorized"]
    assert item["count"] == 2
    assert item["jump"] == {"categories": [DEFAULT_CATEGORY]}


def test_missing_merchant_and_zero_amount(db):
    _seed(
        make_bill_records(2, prefix="DC-NOM", merchant="")
        + make_bill_records(1, prefix="DC-ZERO", amount=0)
        + make_bill_records(1, prefix="DC-OK", merchant="正常商户", amount=10)
    )
    items = _items(data_check_service.data_check(USER_A, today=TODAY))
    assert items["missing_merchant"]["count"] == 2
    assert items["missing_merchant"]["jump"] is None  # 流水筛选表达不了空商户
    assert items["zero_amount"]["count"] == 1


def test_stale_months_skip_pre_account_months(db):
    """未记账月份：从首个记账月起查缺，且当前月未记账也算提醒"""
    _seed(
        make_bill_records(1, prefix="DC-M5", tx_time="2026-05-10 10:00:00")
        + make_bill_records(1, prefix="DC-M7", tx_time="2026-07-10 10:00:00")
    )
    item = _items(data_check_service.data_check(USER_A, today=TODAY))["stale_months"]
    assert item["count"] == 3
    assert item["detail"] == ["2026-06", "2026-08", "2026-09"]

    # 新用户：开户（首笔记账）之前的月份不倒查
    BillDAO.insert_many(
        make_bill_records(1, prefix="DC-NEW", tx_time="2026-08-10 10:00:00"),
        "99901",
    )
    fresh = _items(data_check_service.data_check("99901", today=TODAY))["stale_months"]
    assert fresh["detail"] == ["2026-09"]


def test_duplicates_grouping_and_jump(db):
    _seed(
        make_bill_records(
            2,
            prefix="DC-DUP",
            tx_time="2026-09-05 10:00:00",
            merchant="重复商户",
            amount=88,
        )
        # 退款对：同日同商户同金额但收支类型不同，不成组
        + make_bill_records(
            1,
            prefix="DC-REF-E",
            tx_time="2026-09-06 10:00:00",
            tx_type="expense",
            merchant="退款商户",
            amount=100,
        )
        + make_bill_records(
            1,
            prefix="DC-REF-I",
            tx_time="2026-09-06 12:00:00",
            tx_type="income",
            merchant="退款商户",
            amount=100,
        )
        # 转账不参与重复扫描
        + make_bill_records(
            2,
            prefix="DC-TRF",
            tx_time="2026-09-07 10:00:00",
            tx_type="transfer",
            merchant="转账对方",
            amount=500,
        )
    )
    item = _items(data_check_service.data_check(USER_A, today=TODAY))["duplicates"]
    assert item["count"] == 1
    group = item["detail"][0]
    assert group["merchant"] == "重复商户" and group["count"] == 2
    assert group["jump"] == {
        "start": "2026-09-05",
        "end": "2026-09-05",
        "merchants": ["重复商户"],
    }


def test_unowned_only_for_gateway_account(db):
    """无归属流水：网关账号可见（含处理入口提示），本地模式（user_id=""）跳过"""
    BillDAO.insert_many(
        make_bill_records(2, prefix="DC-ORPHAN"), ""
    )  # 无归属（user_id 为空串）
    gateway = _items(data_check_service.data_check(USER_A, today=TODAY))
    assert gateway["unowned"]["count"] == 2

    local = data_check_service.data_check("", today=TODAY)
    assert all(i["key"] != "unowned" for i in local["items"])
