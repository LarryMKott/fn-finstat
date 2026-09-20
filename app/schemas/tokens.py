"""开放 API Token（T-1.2）的请求 / 响应模型"""

from typing import Optional

from pydantic import BaseModel, Field


class TokenCreate(BaseModel):
    """签发 Token：明文仅随本次响应返回一次，请妥善保存"""

    name: str = Field(
        ..., min_length=1, max_length=64, description="Token 名称（备注用途）"
    )


class TokenOut(BaseModel):
    """Token 条目（不含明文；token_prefix 便于识别）"""

    id: int
    name: str
    token_prefix: str
    created_at: float
    last_used_at: Optional[float] = None
    revoked: bool = False


class TokenCreated(BaseModel):
    """签发结果：token 为明文，仅此一次返回"""

    id: int
    name: str
    token: str
    token_prefix: str
    created_at: float


class TokenRevokeResult(BaseModel):
    """撤销结果"""

    ok: bool
