"""开放 API Token 管理接口（T-1.2）：每个账号管理自己的只读 Token"""

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, request_db_session
from app.schemas.common import ApiResponse, ok
from app.schemas.tokens import (
    TokenCreated,
    TokenCreate,
    TokenOut,
    TokenRevokeResult,
)
from app.services import token_service

router = APIRouter(
    prefix="/api/tokens",
    tags=["开放 API Token"],
    dependencies=[Depends(request_db_session)],
)


@router.get(
    "",
    response_model=ApiResponse[list[TokenOut]],
    summary="我的 Token 列表（不含明文）",
)
def list_tokens(user: CurrentUser):
    return ok(token_service.list_tokens(user.user_id))


@router.post(
    "",
    response_model=ApiResponse[TokenCreated],
    status_code=201,
    summary="签发只读 Token（明文仅此一次返回）",
)
def create_token(user: CurrentUser, payload: TokenCreate):
    return ok(token_service.create_token(payload, user.user_id))


@router.delete(
    "/{token_id}",
    response_model=ApiResponse[TokenRevokeResult],
    summary="撤销 Token（立即失效）",
)
def revoke_token(user: CurrentUser, token_id: int):
    return ok({"ok": token_service.revoke_token(token_id, user.user_id)})
