"""账单流水请求/响应模型"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class BillCreate(BaseModel):
    """手动新增账单请求体（账单导入直接构造 dict，不走此模型）"""

    tx_time: str = Field(..., description="交易时间，如 2024-01-01 12:30:00")
    account: Literal["wechat", "alipay"] = Field("wechat", description="账户类型：wechat/alipay")
    tx_type: Literal["expense", "income", "transfer"] = Field("expense", description="收支类型：expense/income/transfer")
    merchant: str = Field("", max_length=100, description="交易对方/商户名称")
    amount: float = Field(..., gt=0, description="金额（正数）")
    category: str = Field("其他", max_length=20, description="消费分类")
    tx_id: str = Field("", max_length=64, description="交易唯一流水号")
    remark: str = Field("", max_length=200, description="备注")


class BillUpdate(BaseModel):
    """部分更新：长度约束与 BillCreate 保持一致（各列宽：merchant 256/category 64/remark 512/tx_id 64）"""

    tx_time: Optional[str] = None
    account: Optional[Literal["wechat", "alipay"]] = None
    tx_type: Optional[Literal["expense", "income", "transfer"]] = None
    merchant: Optional[str] = Field(None, max_length=100)
    amount: Optional[float] = Field(None, gt=0)
    category: Optional[str] = Field(None, max_length=20)
    tx_id: Optional[str] = Field(None, max_length=64)
    remark: Optional[str] = Field(None, max_length=200)


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
