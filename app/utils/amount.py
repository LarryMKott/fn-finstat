"""金额处理：写入归一化与展示取整（统计 / 预算 / AI 报告共用 round2）"""

from __future__ import annotations


def normalize_amount(amount) -> float:
    """将金额四舍五入到 2 位小数；非数值或负数原样返回（由调用方校验）"""
    try:
        return round(float(amount), 2)
    except (TypeError, ValueError):
        return amount


def round2(value) -> float:
    """金额统一保留 2 位小数（None/空按 0 处理，抵消浮点存储误差）"""
    return round(float(value or 0), 2)
