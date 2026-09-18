"""账本接口的数据模型（T-7.1 账本维度）

owner_id 暂不对外暴露：账本归属账号要等 T-7.2 家庭空间确定后才有意义，
当前所有账本为应用级共享（owner_id 为空串）。
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class LedgerCreate(BaseModel):
    """新建账本：账本名全局唯一"""

    name: str = Field(..., max_length=64, description="账本名（全局唯一）")
    remark: str = Field("", max_length=255, description="备注")


class LedgerUpdate(BaseModel):
    """部分更新账本（仅传需要改的字段）"""

    name: Optional[str] = Field(None, max_length=64, description="账本名")
    remark: Optional[str] = Field(None, max_length=255, description="备注")


class LedgerOut(BaseModel):
    """账本列表项：bill_count 为该账本下未删除的流水条数"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_default: bool = False
    remark: str = ""
    bill_count: int = 0


class LedgerDeleteResult(BaseModel):
    """删除账本结果：数据已并入默认账本（各项为并入默认账本的条数）

    dropped_budgets 为被丢弃的预算行数：目标账本已有同键预算（账号+月份+分类）
    时保留目标、丢弃来源——预算是「设置」而非「事实数据」，不合并累加。
    """

    id: int
    moved_to_default: bool = True
    moved_bills: int = 0
    moved_budgets: int = 0
    moved_assets: int = 0
    dropped_budgets: int = 0
