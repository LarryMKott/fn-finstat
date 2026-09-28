"""智能分类（DeepSeek）接口的数据模型"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class AIProviderInfo(BaseModel):
    """供应商预置信息（供设置页下拉与默认值回填；均为公开的接入常识信息）"""

    value: str
    label: str
    base_url: str = ""
    models: list[str] = []
    key_url: str = ""


class AIConfigUpdate(BaseModel):
    """设置页保存/测试 AI 配置：api_key 为 None 表示保持不变，空串表示清除"""

    enabled: Optional[bool] = None
    auto_report_enabled: Optional[bool] = None
    provider: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None


class AIConfigOut(BaseModel):
    """AI 配置回显（密钥不回传明文，只给掩码提示；providers 仅管理员可见）

    auto_report_generated 为本月自动生成的报告份数（AI-4 费用可见性），
    非敏感信息，对全部登录用户可见。
    """

    enabled: bool
    auto_report_enabled: bool = True
    has_api_key: bool
    provider: str = "deepseek"
    api_key_hint: str = ""
    base_url: str
    model: str
    auto_report_generated: int = 0
    providers: list[AIProviderInfo] = []


class AITestResult(BaseModel):
    """连通性测试结果：失败时 ok=False、message 为原因（不抛错，便于界面直接展示）"""

    ok: bool
    message: str


class AIClassifyRequest(BaseModel):
    """批量重新归类请求：unmatched=仅分类为「其他」的流水，all=全部流水

    after_id 供 scope=all 游标续跑：单次有数量/时间预算，超预算或满页时
    返回 next_after_id，调用方下次携带它继续，避免重头重复计费。
    """

    scope: Literal["unmatched", "all"] = "unmatched"
    after_id: Optional[int] = Field(
        None, ge=1, description="scope=all 时从该流水 id 之后继续（上一轮返回值）"
    )


class AIClassifyResult(BaseModel):
    """批量重新归类结果：processed 本次检查条数，changed 实际改写分类条数

    completed=False 表示还有剩余（时间预算耗尽或满页），next_after_id 为
    续跑游标（scope=unmatched 恒为 None——归类后的流水离开筛选，重扫天然前进）。
    """

    processed: int
    changed: int
    message: str = ""
    completed: bool = True
    next_after_id: Optional[int] = None


class AIReportRequest(BaseModel):
    """月度消费报告请求：month 缺省时默认分析上个月（旧接口，保留兼容）"""

    month: Optional[str] = Field(None, description="统计月份，如 2026-09；缺省为上个月")


class AIReportResult(BaseModel):
    """月度消费报告结果：report 为 Markdown 文本（旧接口，保留兼容）"""

    month: str
    report: str


# ---- 周期报告扩展（月/季/半年/年）+ 归档 ----


# 周期类型枚举：与 app.utils.period.PERIOD_TYPES 严格一致
PeriodType = Literal["month", "quarter", "half", "year"]


class AIReportGenerateRequest(BaseModel):
    """周期报告生成请求：period_value 缺省时由 service 按当前周期回退上一周期"""

    period_type: PeriodType = Field(
        ..., description="周期类型：month/quarter/half/year"
    )
    period_value: Optional[str] = Field(
        None,
        description="周期标识，如 2026-09 / 2026-Q1 / 2026-H1 / 2026；缺省为该类型上一周期",
    )


class AIReportGenerateResult(BaseModel):
    """周期报告生成结果：含 Markdown 正文、人类可读标题与统计上下文（口径溯源）"""

    period_type: PeriodType
    period_value: str
    title: str
    report: str
    # 报告引用的全部汇总数字（后端计算、模型只做解释）：前端可据此展开查看来源
    context: dict


class AIReportArchiveRequest(BaseModel):
    """归档报告请求：把生成预览得到的报告落库（按唯一键覆盖旧版本）"""

    period_type: PeriodType
    period_value: str
    title: str
    content: str = Field(..., description="Markdown 报告正文")
    # dict 由路由层 json.dumps 后存 stats_summary 字段；保字段为 dict 便于校验
    stats_summary: Optional[dict] = Field(
        None, description="统计上下文（生成时返回的 context），可选"
    )


class AIReportArchiveOut(BaseModel):
    """归档报告列表项：不含正文，避免列表接口传输 Markdown"""

    id: int
    period_type: PeriodType
    period_value: str
    title: str
    created_at: float
    updated_at: float


class AIReportDetail(BaseModel):
    """归档报告详情：列表项 + 正文 + 统计上下文 JSON 字符串"""

    id: int
    period_type: PeriodType
    period_value: str
    title: str
    content: str
    stats_summary: str
    created_at: float
    updated_at: float
