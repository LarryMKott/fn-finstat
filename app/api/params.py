"""路由层共享查询参数别名：用 Annotated 别名替代逐字重复的 Query 定义

只收拢「跨端点同名同义」的参数（参数名与 OpenAPI 文档保持不变）；
默认值不在 Query 里给（避免与签名默认值冲突），使用处统一 `= None`。
端点私有参数（limit / year / month 等）仍在各自端点声明。
"""

from typing import Annotated, Optional

from fastapi import Query

StartQuery = Annotated[Optional[str], Query(description="起始时间，如 2024-01-01")]
EndQuery = Annotated[Optional[str], Query(description="结束时间，如 2024-12-31")]
AccountQuery = Annotated[
    Optional[str], Query(description="账户类型：wechat/alipay/jd/unionpay")
]
TxTypeQuery = Annotated[
    Optional[str], Query(description="收支类型：expense/income/transfer")
]
# 账本维度（T-7.1）：账本应用级共享、无归属隔离，只是普通筛选维度
LedgerIdQuery = Annotated[
    Optional[int], Query(ge=1, description="账本 id；不传 = 不按账本过滤")
]
