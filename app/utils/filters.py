"""通用查询条件构建（SQLAlchemy ORM 表达式）"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import ColumnElement

from app.db.models import Bill


def build_criteria(
    start: Optional[str] = None,
    end: Optional[str] = None,
    account: Optional[str] = None,
    tx_type: Optional[str] = None,
    category: Optional[str] = None,
    user_id: Optional[str] = None,
) -> list[ColumnElement[bool]]:
    """按可空筛选条件生成 WHERE 表达式列表（user_id 为数据归属账号）"""
    conds: list[ColumnElement[bool]] = []
    if user_id is not None:
        conds.append(Bill.user_id == user_id)
    if start:
        conds.append(Bill.tx_time >= start)
    if end:
        if len(end) == 10:
            # 纯日期条件：tx_time 带时分秒，用次日零点作上界（<）可完整包含当天全部记录，
            # 避免 "23:59:59" 边界遗漏含毫秒或恰好落在该秒的交易
            try:
                next_day = (datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            except ValueError:
                # 非法日期（如 2024-02-30）直接拒绝，避免静默改变筛选语义
                raise HTTPException(status_code=400, detail=f"无效的结束日期：{end}")
            conds.append(Bill.tx_time < next_day)
        else:
            conds.append(Bill.tx_time <= end)
    if account:
        conds.append(Bill.account == account)
    if tx_type:
        conds.append(Bill.tx_type == tx_type)
    if category:
        conds.append(Bill.category == category)
    return conds
