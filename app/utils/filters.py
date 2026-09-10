"""通用查询条件构建"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException


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
        if len(end) == 10:
            # 纯日期条件：tx_time 带时分秒，用次日零点作上界（<）可完整包含当天全部记录，
            # 避免 "23:59:59" 边界遗漏含毫秒或恰好落在该秒的交易
            try:
                next_day = (datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            except ValueError:
                # 非法日期（如 2024-02-30）直接拒绝，避免静默改变筛选语义
                raise HTTPException(status_code=400, detail=f"无效的结束日期：{end}")
            conds.append("tx_time < ?")
            params.append(next_day)
        else:
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
