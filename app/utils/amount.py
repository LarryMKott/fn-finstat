"""金额处理：文本解析、写入归一化与展示取整（解析器 / 统计 / 预算 / AI 报告共用）"""

from __future__ import annotations


def parse_amount(text) -> float | None:
    """金额文本 → 浮点（保留符号）；无法解析返回 None（由调用方决定跳过或报错）

    与 normalize_amount 的区别：解析失败返回 None 而不是把原输入原样带回，
    调用方无需再防御「拿到的是字符串」的隐式契约。
    """
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def normalize_amount(amount) -> float:
    """将金额四舍五入到 2 位小数；非数值或负数原样返回（由调用方校验）"""
    try:
        return round(float(amount), 2)
    except (TypeError, ValueError):
        return amount


def round2(value) -> float:
    """金额统一保留 2 位小数（None/空按 0 处理，抵消浮点存储误差）"""
    return round(float(value or 0), 2)
