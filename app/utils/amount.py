"""金额归一化：所有写入数据库的金额统一四舍五入到 2 位小数

SQLite REAL / MySQL DOUBLE / PostgreSQL DOUBLE PRECISION 均为二进制浮点，
直接存储 0.1 这类十进制小数会有精度误差；在写入边界统一 round 到 2 位小数
可避免误差随交易累积，配合统计层 _round2 保证展示与汇总正确。
"""


def normalize_amount(amount) -> float:
    """将金额四舍五入到 2 位小数；非数值或负数原样返回（由调用方校验）"""
    try:
        return round(float(amount), 2)
    except (TypeError, ValueError):
        return amount
