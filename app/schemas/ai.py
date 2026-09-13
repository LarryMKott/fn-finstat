"""智能分类（DeepSeek）接口的数据模型"""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class AIConfigUpdate(BaseModel):
    """设置页保存/测试 AI 配置：api_key 为 None 表示保持不变，空串表示清除"""

    enabled: Optional[bool] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None


class AIConfigOut(BaseModel):
    """AI 配置回显（密钥不回传明文，只给掩码提示）"""

    enabled: bool
    has_api_key: bool
    api_key_hint: str = ""
    base_url: str
    model: str


class AITestResult(BaseModel):
    """连通性测试结果：失败时 ok=False、message 为原因（不抛错，便于界面直接展示）"""

    ok: bool
    message: str


class AIClassifyRequest(BaseModel):
    """批量重新归类请求：unmatched=仅分类为「其他」的流水，all=全部流水"""

    scope: Literal["unmatched", "all"] = "unmatched"


class AIClassifyResult(BaseModel):
    """批量重新归类结果：processed 本次检查条数，changed 实际改写分类条数"""

    processed: int
    changed: int
    message: str = ""


class AIReportRequest(BaseModel):
    """月度消费报告请求：month 缺省时默认分析上个月"""

    month: Optional[str] = Field(None, description="统计月份，如 2026-09；缺省为上个月")


class AIReportResult(BaseModel):
    """月度消费报告结果：report 为 Markdown 文本"""

    month: str
    report: str
