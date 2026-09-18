"""自然语言查账（T-6.1 查询意图翻译层 / T-6.2 对话式界面）的数据模型

安全模型：请求只接受 question 与追问上下文（extra="forbid"，多余字段直接 422）；
结构化查询对象（NLQuerySpec）的全部枚举/数组在服务层经白名单校验后才执行，
模型输出永远不会拼接进 SQL，user_id 由服务端强制注入。
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# 指标：expense=支出总额 / income=收入总额 / count=流水笔数
QueryMetric = Literal["expense", "income", "count"]
# 分组维度：none=不分组 / category / merchant / month / day
QueryGroupBy = Literal["none", "category", "merchant", "month", "day"]
# 分组排序：amount=按组内金额、count=按组内笔数、key=按分组键升序
QueryOrderBy = Literal[
    "amount_desc", "amount_asc", "count_desc", "count_asc", "key_asc"
]


class NLQueryTime(BaseModel):
    """时间口径：两端日期均为 YYYY-MM-DD 且按「含当日」语义执行；label 为人类可读口径"""

    start: Optional[str] = None
    end: Optional[str] = None
    label: str = ""


class NLQuerySpec(BaseModel):
    """结构化查询对象（口径）：回答卡片必须原样展示，让用户可校验答案怎么算的"""

    time: NLQueryTime
    categories: list[str] = Field(default_factory=list, max_length=3)
    merchants: list[str] = Field(default_factory=list, max_length=3)
    metric: QueryMetric = "expense"
    group_by: QueryGroupBy = "none"
    order_by: QueryOrderBy = "amount_desc"
    limit: int = Field(10, ge=1, le=100)


class NLQueryHistoryItem(BaseModel):
    """追问上下文单项：上一轮的问题与服务端回传的口径

    spec 形状由本模型校验，值在服务层再过一次白名单（枚举/真实分类/长度），
    不因「是自己上一轮的输出」而豁免篡改校验。
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=1, max_length=200)
    spec: NLQuerySpec


class NLQueryRequest(BaseModel):
    """一句话查账请求：question + 可选追问上下文（最近 3 轮），拒绝任何试图夹带筛选/身份的字段"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=1, max_length=200, description="自然语言问题")
    history: list[NLQueryHistoryItem] = Field(
        default_factory=list, max_length=3, description="追问上下文（最近 3 轮）"
    )


class NLQueryRow(BaseModel):
    """分组聚合行：key 为分类名/商户名/YYYY-MM/YYYY-MM-DD"""

    key: str
    amount: float
    count: int


class NLQueryDetail(BaseModel):
    """参与计算的流水明细样本（按金额降序，口径透明）"""

    id: int
    tx_time: str
    account: str
    tx_type: str
    merchant: str
    category: str
    amount: float


class NLQueryResult(BaseModel):
    """一句话查账结果：spec 为口径、answer 为后端生成的确定性结论（数字全部来自数据库）"""

    question: str
    spec: NLQuerySpec
    # rule=规则路径（零成本）；llm=模型翻译口径；fallback=模型不可用降级规则
    source: Literal["rule", "llm", "fallback"]
    degraded: bool = False
    message: str = ""
    # 追问时从上一轮继承的口径维度：time / merchants / categories（T-6.2）
    inherited: list[str] = Field(default_factory=list)
    total: float
    count: int
    grouped: list[NLQueryRow] = Field(default_factory=list)
    truncated: bool = False
    details: list[NLQueryDetail] = Field(default_factory=list)
    answer: str
