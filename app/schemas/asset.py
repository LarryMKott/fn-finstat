"""资产快照接口的数据模型"""

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import DEFAULT_LEDGER_ID

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def valid_date(date: str) -> bool:
    if not DATE_PATTERN.match(date or ""):
        return False
    try:
        from datetime import date as _date

        _date.fromisoformat(date)
        return True
    except ValueError:
        return False


class AssetSnapshotCreate(BaseModel):
    """新增资产快照：name 为账户/条目名（如 招商储蓄卡），负债金额记正数"""

    snap_date: str = Field(..., description="快照日期，如 2026-09-01")
    name: str = Field(..., min_length=1, max_length=32, description="账户/条目名")
    asset_type: Literal["asset", "liability"] = Field(
        "asset", description="asset=资产 / liability=负债"
    )
    amount: float = Field(..., ge=0, description="金额（负债记正数）")
    remark: str = Field("", max_length=100, description="备注")
    ledger_id: Optional[int] = Field(
        None, ge=1, description="账本 id（T-7.1）；不传落到默认账本"
    )


class AssetSnapshotUpdate(BaseModel):
    """部分更新资产快照"""

    snap_date: Optional[str] = None
    name: Optional[str] = Field(None, min_length=1, max_length=32)
    asset_type: Optional[Literal["asset", "liability"]] = None
    amount: Optional[float] = Field(None, ge=0)
    remark: Optional[str] = Field(None, max_length=100)
    ledger_id: Optional[int] = Field(
        None,
        ge=1,
        description="账本 id（T-7.1）：传入即把快照移入该账本；不传/null 保持不变",
    )


class AssetSnapshotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    snap_date: str
    name: str
    asset_type: str
    amount: float
    remark: str = ""
    ledger_id: int = DEFAULT_LEDGER_ID


class AssetTrendPoint(BaseModel):
    """净资产趋势单点：net = assets - liabilities"""

    date: str
    assets: float
    liabilities: float
    net: float
