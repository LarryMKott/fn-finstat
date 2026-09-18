"""自然语言查账接口：一句话问题 → 意图翻译 → 只读统计（T-6.1）"""

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, request_db_session
from app.schemas.common import ApiResponse, ok
from app.schemas.nl_query import NLQueryRequest, NLQueryResult
from app.services import nl_query

router = APIRouter(
    prefix="/api/nl-query",
    tags=["智能查账"],
    dependencies=[Depends(request_db_session)],
)


@router.post(
    "",
    response_model=ApiResponse[NLQueryResult],
    summary="一句话查账（自然语言 → 结构化查询）",
)
def ask(user: CurrentUser, payload: NLQueryRequest):
    """规则路径零成本优先；未命中走 LLM 意图翻译（仅产出查询对象，不参与计算）。

    查询一律限定当前账号：user_id 由服务端从网关身份注入，接口不接受任何
    身份/越权参数（请求体 extra="forbid"）；模型不可用时降级规则路径并明示。
    history 为追问上下文（≤3 轮，schema 校验 + 服务层白名单双重收敛）。
    """
    return ok(
        nl_query.query(
            user.user_id,
            payload.question,
            [
                {"question": h.question, "spec": h.spec.model_dump()}
                for h in payload.history
            ],
        )
    )
