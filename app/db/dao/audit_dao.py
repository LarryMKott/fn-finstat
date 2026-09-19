"""操作审计数据访问层（T-7.6）：写入（含过期清理）与查询"""

from typing import Optional

from sqlalchemy import delete, func, select

from app.db.base import get_db
from app.db.models import AuditLog


class AuditDAO:
    @staticmethod
    def create(
        user_id: str,
        action: str,
        entity: str,
        entity_id: Optional[str],
        summary: str,
        created_at: float,
    ) -> dict:
        """写入一条审计并清理保留窗口外的过期行（同一事务）"""
        with get_db() as session:
            log = AuditLog(
                user_id=user_id,
                action=action,
                entity=entity,
                entity_id=entity_id,
                summary=summary,
                created_at=created_at,
            )
            session.add(log)
            session.flush()
            out = log.as_dict()
        return out

    @staticmethod
    def purge_older_than(created_at: float) -> int:
        """清理保留窗口外的过期审计行，返回删除条数"""
        with get_db() as session:
            return session.execute(
                delete(AuditLog).where(AuditLog.created_at < created_at)
            ).rowcount

    @staticmethod
    def list_logs(
        user_id: Optional[str],
        action: Optional[str],
        limit: int,
        offset: int,
    ) -> tuple[int, list[dict]]:
        """按时间倒序查询审计；user_id 为 None 时查全部账号

        返回 (总数, 审计列表)；总数为过滤条件下的全量条数（分页用）。
        """
        conds = []
        if user_id is not None:
            conds.append(AuditLog.user_id == user_id)
        if action:
            conds.append(AuditLog.action == action)
        with get_db() as session:
            total = session.scalar(
                select(func.count()).select_from(AuditLog).where(*conds)
            )
            stmt = (
                select(AuditLog)
                .where(*conds)
                .order_by(AuditLog.id.desc())
                .limit(limit)
                .offset(offset)
            )
            rows = [r.as_dict() for r in session.scalars(stmt)]
        return total or 0, rows
