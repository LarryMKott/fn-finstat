"""金额归一化测试"""

import pytest

from app.utils.amount import normalize_amount


def test_rounds_to_two_decimals():
    assert normalize_amount("9.90") == 9.9
    assert normalize_amount(1.005) == 1.0  # 二进制浮点下 1.005 实际略小
    assert normalize_amount(0.1 + 0.2) == 0.3
    assert normalize_amount(1234.567) == 1234.57


def test_accepts_numeric_strings_with_spaces():
    assert normalize_amount(" 12 ") == 12.0
    assert normalize_amount("0") == 0.0
    assert normalize_amount(-5.567) == -5.57  # 负数仍按数值处理，合法性由调用方校验


@pytest.mark.parametrize("bad", [None, "", "abc", object()])
def test_invalid_input_passthrough(bad):
    """非数值原样返回，由调用方负责校验"""
    result = normalize_amount(bad)
    assert result is bad or result == bad
