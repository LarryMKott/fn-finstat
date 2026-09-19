"""报销 / 垫付工作流（T-7.4）的请求 / 响应模型"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

ReimbStatus = Literal["pending", "submitted", "partial", "settled"]


class ReimbursementCreate(BaseModel):
    """新建报销单（固定待提交状态；流水随后挂入）"""

    title: str = Field(..., min_length=1, max_length=64, description="报销单名称")
    note: str = Field("", max_length=255, description="备注")


class ReimbursementUpdate(BaseModel):
    """更新报销单：字段均可选，仅传入的字段生效

    状态推进到部分到账 / 已结清时必须携带 received_amount（到账金额）；
    回到待提交 / 已提交时到账登记由服务端清空。
    """

    title: Optional[str] = Field(None, min_length=1, max_length=64)
    note: Optional[str] = Field(None, max_length=255)
    status: Optional[ReimbStatus] = None
    received_amount: Optional[float] = Field(None, ge=0, description="到账金额（元）")
    received_date: Optional[str] = Field(
        None, description="到账日期，YYYY-MM-DD；null = 清空"
    )


class ReimbursementOut(BaseModel):
    """报销单（含关联流水的笔数与金额合计）"""

    id: int
    title: str
    status: str
    status_label: str = ""
    note: str = ""
    received_amount: Optional[float] = None
    received_date: Optional[str] = None
    created_at: float
    bill_count: int = 0
    total_amount: float = 0


class ReimbBillIdsRequest(BaseModel):
    """挂单 / 摘单的流水 id 列表"""

    ids: list[int] = Field(..., min_length=1, max_length=1000)


class ReimbAttachResult(BaseModel):
    """挂单 / 摘单结果：实际受影响条数"""

    claim_id: int
    attached: int = 0
    detached: int = 0


class ReimbBill(BaseModel):
    """报销单内流水明细行"""

    id: int
    tx_time: str
    merchant: str
    category: str
    amount: float
    reimbursed: bool


class ReimbBillList(BaseModel):
    """报销单内流水明细：total 为金额合计，count 为笔数"""

    claim_id: int
    total: float
    count: int
    rows: list[ReimbBill]
