"""AI 财务教练接口（T-1.6）：基于结构化摘要的对话式财务建议

隐私红线：上下文只包含本地聚合的结构化摘要，原始流水永不外传。
"""

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, request_db_session
from app.schemas.common import ApiResponse, ok
from app.schemas.coach import CoachChatRequest, CoachChatResult
from app.services import coach_service

router = APIRouter(
    prefix="/api/coach",
    tags=["AI 财务教练"],
    dependencies=[Depends(request_db_session)],
)


@router.post(
    "/chat",
    response_model=ApiResponse[CoachChatResult],
    summary="AI 财务教练对话（基于结构化摘要，不外传原始流水）",
)
def coach_chat(user: CurrentUser, payload: CoachChatRequest):
    """需要已在设置页配置 DeepSeek API Key；追问上下文由前端传入（≤3 轮）"""
    return ok(
        coach_service.coach_chat(
            user.user_id,
            payload.question,
            [{"question": h.question, "answer": h.answer} for h in payload.history],
        )
    )
