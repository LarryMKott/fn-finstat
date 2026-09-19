"""账单流水请求/响应模型"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import DEFAULT_LEDGER_ID

AccountField = Literal["wechat", "alipay", "jd", "unionpay"]


class BillCreate(BaseModel):
    """手动新增账单请求体（账单导入直接构造 dict，不走此模型）"""

    tx_time: str = Field(..., description="交易时间，如 2024-01-01 12:30:00")
    account: AccountField = Field(
        "wechat", description="账户类型：wechat/alipay/jd/unionpay"
    )
    tx_type: Literal["expense", "income", "transfer"] = Field(
        "expense", description="收支类型：expense/income/transfer"
    )
    merchant: str = Field("", max_length=100, description="交易对方/商户名称")
    amount: float = Field(..., gt=0, description="金额（正数）")
    category: str = Field("其他", max_length=20, description="消费分类")
    tx_id: str = Field("", max_length=64, description="交易唯一流水号")
    remark: str = Field("", max_length=200, description="备注")
    tags: str = Field("", max_length=255, description="自定义标签，逗号分隔")
    reimbursed: bool = Field(False, description="报销标记")
    ledger_id: Optional[int] = Field(
        None, ge=1, description="账本 id（T-7.1）；不传落到默认账本"
    )


class BillUpdate(BaseModel):
    """部分更新：长度约束与 BillCreate 保持一致（接口层限制，非数据库列宽）"""

    tx_time: Optional[str] = None
    account: Optional[AccountField] = None
    tx_type: Optional[Literal["expense", "income", "transfer"]] = None
    merchant: Optional[str] = Field(None, max_length=100)
    amount: Optional[float] = Field(None, gt=0)
    category: Optional[str] = Field(None, max_length=20)
    tx_id: Optional[str] = Field(None, max_length=64)
    remark: Optional[str] = Field(None, max_length=200)
    tags: Optional[str] = Field(None, max_length=255)
    reimbursed: Optional[bool] = None
    ledger_id: Optional[int] = Field(
        None,
        ge=1,
        description="账本 id（T-7.1）：传入即把流水移入该账本；不传/null 保持不变",
    )


class BillOut(BaseModel):
    """账单流水响应体"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    tx_time: str
    account: str
    tx_type: str
    merchant: str
    amount: float
    category: str
    tx_id: Optional[str] = None
    remark: str = ""
    tags: str = ""
    reimbursed: bool = False
    ledger_id: int = DEFAULT_LEDGER_ID


class BatchBillRequest(BaseModel):
    """批量操作请求：ids 为流水 id 列表；action 决定其余字段是否生效

    - delete：移入回收站（软删除）；purge：彻底删除（回收站场景）
    - restore：从回收站还原
    - set_category / set_tags / set_reimbursed：分别需要 category / tags / reimbursed
    """

    ids: list[int] = Field(..., min_length=1, max_length=1000)
    action: Literal[
        "delete", "restore", "purge", "set_category", "set_tags", "set_reimbursed"
    ]
    category: Optional[str] = Field(None, max_length=20)
    tags: Optional[str] = Field(None, max_length=255)
    reimbursed: Optional[bool] = None


class BillIdsRequest(BaseModel):
    """仅流水 id 列表的请求体（回收站还原/彻底删除用）"""

    ids: list[int] = Field(..., min_length=1, max_length=1000)


class BatchBillResult(BaseModel):
    """批量操作结果：updated 为实际受影响条数（不存在的 id 不计入）"""

    updated: int
