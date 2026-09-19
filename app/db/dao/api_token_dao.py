"""开放 API Token 数据访问层（T-1.2）：仅存哈希，明文创建时一次性返回"""

from typing import Optional

from sqlalchemy import select, update

from app.db.base import get_db
from app.db.models import ApiToken

# last_used_at 的更新节流（秒）：认证高频，避免每次请求都写库
_TOUCH_INTERVAL = 60


class ApiTokenDAO:
    @staticmethod
    def create(user_id: str, name: str, token_prefix: str, token_hash: str) -> dict:
        import time

        with get_db() as session:
            row = ApiToken(
                user_id=user_id,
                name=name,
                token_prefix=token_prefix,
                token_hash=token_hash,
                created_at=time.time(),
            )
            session.add(row)
            session.flush()
            return row.as_dict()

    @staticmethod
    def find_by_hash(token_hash: str) -> Optional[dict]:
        """按哈希取有效（未撤销）Token；不存在或已撤销返回 None"""
        with get_db() as session:
            row = session.scalar(
                select(ApiToken).where(
                    ApiToken.token_hash == token_hash, ApiToken.revoked.is_(False)
                )
            )
            return row.as_dict() if row is not None else None

    @staticmethod
    def touch_last_used(token_hash: str) -> None:
        """更新最近使用时间（节流：距上次更新不足间隔则跳过，避免高频写）"""
        import time

        with get_db() as session:
            row = session.scalar(
                select(ApiToken).where(ApiToken.token_hash == token_hash)
            )
            if row is None:
                return
            now = time.time()
            if row.last_used_at is None or now - row.last_used_at >= _TOUCH_INTERVAL:
                session.execute(
                    update(ApiToken)
                    .where(ApiToken.id == row.id)
                    .values(last_used_at=now)
                )

    @staticmethod
    def list_by_user(user_id: str) -> list[dict]:
        """当前账号全部 Token（含已撤销），按创建时间倒序"""
        with get_db() as session:
            rows = session.scalars(
                select(ApiToken)
                .where(ApiToken.user_id == user_id)
                .order_by(ApiToken.created_at.desc(), ApiToken.id.desc())
            )
            return [t.as_dict() for t in rows]

    @staticmethod
    def revoke(token_id: int, user_id: str) -> bool:
        """撤销 Token（置 revoked，保留哈希），返回是否存在且未撤销"""
        with get_db() as session:
            row = session.scalar(
                select(ApiToken).where(
                    ApiToken.id == token_id,
                    ApiToken.user_id == user_id,
                    ApiToken.revoked.is_(False),
                )
            )
            if row is None:
                return False
            row.revoked = True
        return True
