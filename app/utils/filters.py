"""通用查询条件构建"""
from typing import Optional


def build_filter(
    start: Optional[str] = None,
    end: Optional[str] = None,
    account: Optional[str] = None,
    tx_type: Optional[str] = None,
    category: Optional[str] = None,
) -> tuple[str, list]:
    """按可空筛选条件拼接 WHERE 子句与参数（参数化查询，防注入）"""
    conds: list[str] = []
    params: list = []
    if start:
        conds.append("tx_time >= ?")
        params.append(start)
    if end:
        conds.append("tx_time <= ?")
        params.append(end)
    if account:
        conds.append("account = ?")
        params.append(account)
    if tx_type:
        conds.append("tx_type = ?")
        params.append(tx_type)
    if category:
        conds.append("category = ?")
        params.append(category)
    where = f"WHERE {' AND '.join(conds)}" if conds else ""
    return where, params
