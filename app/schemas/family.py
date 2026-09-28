"""家庭空间接口的数据模型（T-7.2 家庭空间与合并视图）

昵称是加入时的快照展示字段，允许为空（前端回退展示 user_id）；
邀请码只在家庭管理员视角的 FamilyInfoOut 中非空。
"""

from typing import Optional

from pydantic import BaseModel, Field


class FamilyCreate(BaseModel):
    """创建家庭：创建人自动成为家庭管理员"""

    name: str = Field(..., min_length=1, max_length=64, description="家庭名称")


class FamilyJoin(BaseModel):
    """凭邀请码加入家庭"""

    code: str = Field(..., min_length=4, max_length=16, description="家庭邀请码")


class FamilySettingsUpdate(BaseModel):
    """家庭设置（当前仅明细可见性）：仅家庭管理员可改"""

    allow_detail_view: bool = Field(..., description="是否允许成员互看明细（默认关）")


class FamilyMemberOut(BaseModel):
    """家庭成员项"""

    user_id: str
    role: str = "member"
    nickname: str = ""
    joined_at: float = 0


class FamilyInfoOut(BaseModel):
    """我的家庭信息（未加入家庭时 data 为 null）

    invite_code 仅家庭管理员视角非空；members 按管理员在前排序。
    """

    id: int
    name: str
    allow_detail_view: bool = False
    created_at: float = 0
    members: list[FamilyMemberOut] = []
    my_role: str = "member"
    invite_code: Optional[str] = None


class FamilyMemberStat(FamilyMemberOut):
    """家庭汇总中的逐成员收支（totals 之和 = 全家合计，可逐项核对）"""

    income: float = 0
    expense: float = 0
    net: float = 0
    bill_count: int = 0


class FamilyTotals(BaseModel):
    income: float = 0
    expense: float = 0
    net: float = 0


class FamilyCategoryStat(BaseModel):
    name: str
    value: float = 0


class FamilySummaryOut(BaseModel):
    """家庭月度汇总：totals = 各成员之和；categories 为全员支出分类合并"""

    month: str
    family: dict
    totals: FamilyTotals = FamilyTotals()
    members: list[FamilyMemberStat] = []
    categories: list[FamilyCategoryStat] = []


class FamilyJoinResult(BaseModel):
    family_id: int
    family_name: str


class FamilySettingsOut(BaseModel):
    allow_detail_view: bool = False


class InviteCodeOut(BaseModel):
    invite_code: str


class FamilyAIReviewRequest(BaseModel):
    """家庭月度 AI 复盘请求（AI-10）：指定复盘月份；只读计算不落库"""

    month: str = Field(..., description="复盘月份 YYYY-MM")
