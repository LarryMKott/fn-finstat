"""通用查询条件构建测试"""

import pytest

from app.core.errors import ValidationError
from app.utils.filters import build_criteria


def compile_sql(cond) -> str:
    return str(cond.compile(compile_kwargs={"literal_binds": True}))


DELETED_COND = "bills.deleted IS false"


def test_default_excludes_deleted():
    """默认只查未删除流水（回收站场景显式传 include_deleted=True）"""
    conds = build_criteria()
    assert len(conds) == 1
    assert DELETED_COND in compile_sql(conds[0])
    conds = build_criteria(include_deleted=True, user_id="u1")
    assert len(conds) == 1
    assert "user_id = 'u1'" in compile_sql(conds[0])


def test_tag_and_reimbursed_filters():
    conds = build_criteria(include_deleted=True, tag="出差")
    assert len(conds) == 1
    assert "%,出差,%" in compile_sql(conds[0])
    conds = build_criteria(include_deleted=True, reimbursed=True)
    assert "bills.reimbursed IS true" in compile_sql(conds[0])


def test_each_single_filter():
    cases = [
        ({"user_id": "u1"}, "user_id = 'u1'"),
        ({"start": "2024-01-01"}, "tx_time >= '2024-01-01'"),
        ({"end": "2024-01-01 23:59:59"}, "tx_time <= '2024-01-01 23:59:59'"),
        ({"account": "wechat"}, "account = 'wechat'"),
        ({"tx_type": "expense"}, "tx_type = 'expense'"),
        ({"category": "餐饮"}, "category = '餐饮'"),
    ]
    for kwargs, expected in cases:
        conds = build_criteria(include_deleted=True, **kwargs)
        assert len(conds) == 1
        assert expected in compile_sql(conds[0])


def test_date_only_end_uses_next_day_upper_bound():
    """纯日期结束条件用次日零点作上界（<），完整覆盖当天记录"""
    conds = build_criteria(include_deleted=True, end="2024-03-15")
    assert "< '2024-03-16'" in compile_sql(conds[0])


@pytest.mark.parametrize("bad", ["2024-02-30", "not-a-date", "2024-13-01"])
def test_invalid_date_only_end_rejected(bad):
    with pytest.raises(ValidationError) as exc_info:
        build_criteria(end=bad)
    assert exc_info.value.http_status == 400


def test_all_filters_combined_in_order():
    conds = build_criteria(
        start="2024-01-01",
        end="2024-06-30",
        account="alipay",
        tx_type="income",
        category="工资",
        user_id="u9",
        include_deleted=True,
    )
    assert len(conds) == 6
    sql = " AND ".join(compile_sql(c) for c in conds)
    assert "user_id = 'u9'" in sql
    assert "tx_time >= '2024-01-01'" in sql
    assert "tx_time < '2024-07-01'" in sql
    assert "account = 'alipay'" in sql
    assert "tx_type = 'income'" in sql
    assert "category = '工资'" in sql


def test_invalid_start_date_rejected():
    """start 与 end 同语义：非法日期拒绝而非静默改变筛选范围"""
    import pytest

    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        build_criteria(start="2026-13-45")
