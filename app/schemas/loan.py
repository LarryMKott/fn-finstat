"""借贷台账（T-7.5）的请求 / 响应模型"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

LoanDirection = Literal["lend", "borrow"]


class LoanCreate(BaseModel):
    """登记一笔借出（应收）/ 借入（应付）"""

    direction: LoanDirection = Field(
        ..., description="lend=借出（别人欠我）/ borrow=借入（我欠别人）"
    )
    counterparty: str = Field(..., min_length=1, max_length=64, description="对方名称")
    principal: float = Field(..., gt=0, description="本金（元）")
    loan_date: str = Field(..., description="借贷日期，YYYY-MM-DD")
    due_date: Optional[str] = Field(None, description="约定还款日，YYYY-MM-DD（可选）")
    note: str = Field("", max_length=255, description="备注")


class LoanUpdate(BaseModel):
    """更新借贷信息：字段均可选，仅传入的字段生效"""

    counterparty: Optional[str] = Field(None, min_length=1, max_length=64)
    principal: Optional[float] = Field(None, gt=0)
    loan_date: Optional[str] = None
    due_date: Optional[str] = None
    note: Optional[str] = Field(None, max_length=255)


class LoanPaymentCreate(BaseModel):
    """登记一笔还款"""

    amount: float = Field(..., gt=0, description="还款金额（元）")
    pay_date: str = Field(..., description="还款日期，YYYY-MM-DD")
    note: str = Field("", max_length=255, description="备注")


class LoanPaymentOut(BaseModel):
    """还款记录"""

    id: int
    loan_id: int
    amount: float
    pay_date: str
    note: str = ""
    created_at: float


class LoanPayments(BaseModel):
    """还款明细与进度：repaid 已还合计，remaining 未还；还清即结项"""

    loan_id: int
    principal: float
    repaid: float
    remaining: float
    status: str
    rows: list[LoanPaymentOut]


class LoanOut(BaseModel):
    """借贷条目（含进度）：repaid 已还合计，remaining 未还"""

    id: int
    direction: str
    direction_label: str = ""
    counterparty: str
    principal: float
    repaid: float = 0
    remaining: float = 0
    payment_count: int = 0
    status: str
    loan_date: str
    due_date: Optional[str] = None
    note: str = ""
    created_at: float


class LoanLedger(BaseModel):
    """借贷台账总览：receivable 应收未收合计，payable 应付未还合计"""

    receivable: float
    payable: float
    items: list[LoanOut]
