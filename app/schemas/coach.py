"""AI 财务教练（T-1.6）的请求 / 响应模型"""

from pydantic import BaseModel, Field


class CoachHistoryItem(BaseModel):
    """追问上下文单项：上一轮的问题与回答"""

    question: str = Field(..., min_length=1, max_length=500)
    answer: str = Field(..., min_length=1, max_length=2000)


class CoachChatRequest(BaseModel):
    """AI 教练对话请求：问题 + 可选追问上下文（最近 3 轮）"""

    question: str = Field(..., min_length=1, max_length=500, description="财务问题")
    history: list[CoachHistoryItem] = Field(
        default_factory=list, max_length=3, description="追问上下文（最近 3 轮）"
    )


class CoachChatResult(BaseModel):
    """AI 教练回答"""

    question: str
    context: str = Field(description="本次发给模型的结构化摘要（口径透明）")
    answer: str
