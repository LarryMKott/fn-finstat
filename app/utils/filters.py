"""通用查询条件构建（SQLAlchemy ORM 表达式）

本模块为纯数据访问工具：只依赖 ORM 模型，不依赖 Web 框架；
参数不合法时抛 core.errors.ValidationError，由上层统一转 HTTP 响应。
"""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import ColumnElement

from app.core.errors import ValidationError
from app.db.models import Bill


def build_criteria(
    start: Optional[str] = None,
    end: Optional[str] = None,
    account: Optional[str] = None,
    tx_type: Optional[str] = None,
    category: Optional[str] = None,
    user_id: Optional[str] = None,
    include_deleted: bool = False,
    tag: Optional[str] = None,
    reimbursed: Optional[bool] = None,
) -> list[ColumnElement[bool]]:
    """按可空筛选条件生成 WHERE 表达式列表（user_id 为数据归属账号）

    - 回收站场景用 include_deleted=True 查已软删除流水；默认只看未删除
    - tag 为精确匹配（tags 以逗号分隔存储，两侧补逗号后 LIKE，避免子串误命中）
    - end 为 10 位纯日期时按"含当日"语义处理（tx_time 带时分秒，用次日零点作
      严格上界可完整包含当天全部记录）；调用方传"排他上界"时须带时间部分
    """
    conds: list[ColumnElement[bool]] = []
    if user_id is not None:
        conds.append(Bill.user_id == user_id)
    if not include_deleted:
        conds.append(Bill.deleted.is_(False))
    if start:
        conds.append(Bill.tx_time >= start)
    if end:
        if len(end) == 10:
            # 纯日期条件：tx_time 带时分秒，用次日零点作上界（<）可完整包含当天全部记录，
            # 避免 "23:59:59" 边界遗漏含毫秒或恰好落在该秒的交易
            try:
                next_day = (
                    datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
                ).strftime("%Y-%m-%d")
            except ValueError:
                # 非法日期（如 2024-02-30）直接拒绝，避免静默改变筛选语义
                raise ValidationError(f"无效的结束日期：{end}") from None
            conds.append(Bill.tx_time < next_day)
        else:
            conds.append(Bill.tx_time <= end)
    if account:
        conds.append(Bill.account == account)
    if tx_type:
        conds.append(Bill.tx_type == tx_type)
    if category:
        conds.append(Bill.category == category)
    if tag:
        # 用列自身的 LIKE 四分支（相等/打头/收尾/居中）实现"逗号分隔精确匹配"，
        # 不用 func.concat：旧版 SQLite（<3.44）没有 concat 函数
        escaped = tag.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conds.append(
            (Bill.tags == tag)
            | Bill.tags.like(f"{escaped},%", escape="\\")
            | Bill.tags.like(f"%,{escaped}", escape="\\")
            | Bill.tags.like(f"%,{escaped},%", escape="\\")
        )
    if reimbursed is not None:
        conds.append(Bill.reimbursed.is_(reimbursed))
    return conds
