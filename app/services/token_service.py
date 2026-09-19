"""开放 API Token 服务（T-1.2）：只读凭证的签发 / 认证 / 撤销

- Token 形如 ``ffk_<32 位十六进制>``，明文只在创建响应中出现一次；
- 服务端仅存 SHA-256 哈希（唯一约束 + 撤销标记），库泄露不等于凭证泄露；
- Token 恒为**只读**且**非管理员**：写方法与管理面在权限中间件拒绝；
- 认证：网关可信头（X-Trim-*）优先，Token 仅作为无网关头请求的兜底身份
  （见 api/deps.get_identity 与权限中间件的 Token 分支）。
"""

import hashlib
import secrets

from app.core.context import GatewayUser
from app.core.errors import ErrorCode, ValidationError
from app.db.dao.api_token_dao import ApiTokenDAO
from app.services import audit_service

PREFIX = "ffk_"
NAME_MAX = 64


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_token(payload, user_id: str) -> dict:
    """签发 Token：明文仅随本次响应返回一次"""
    name = (payload.name or "").strip()
    if not name:
        raise ValidationError("请填写 Token 名称", code=ErrorCode.LOAN_INVALID)
    raw = PREFIX + secrets.token_hex(16)
    row = ApiTokenDAO.create(
        user_id=user_id,
        name=name[:NAME_MAX],
        token_prefix=raw[:12],
        token_hash=_hash_token(raw),
    )
    audit_service.record(
        user_id, "token.create", "api_token", row["id"], "签发 API Token「" + name[:NAME_MAX] + "」"
    )
    return {**{k: v for k, v in row.items()}, "token": raw}


def list_tokens(user_id: str) -> list[dict]:
    """当前账号的 Token 列表（不含明文）"""
    return ApiTokenDAO.list_by_user(user_id)


def revoke_token(token_id: int, user_id: str) -> bool:
    """撤销 Token；不存在或已撤销返回 False"""
    revoked = ApiTokenDAO.revoke(token_id, user_id)
    if revoked:
        audit_service.record(
            user_id, "token.revoke", "api_token", token_id, "撤销 API Token"
        )
    return revoked


def authenticate(raw_token: str) -> GatewayUser | None:
    """Token 明文 → 网关身份（恒为非管理员）；无效 / 已撤销返回 None"""
    row = ApiTokenDAO.find_by_hash(_hash_token(raw_token))
    if row is None:
        return None
    ApiTokenDAO.touch_last_used(_hash_token(raw_token))
    return GatewayUser(user_id=row["user_id"], user_name="", is_admin=False)
