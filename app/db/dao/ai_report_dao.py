"""AI 报告归档数据访问层（SQLAlchemy ORM，按 user_id 隔离账号）

参考 budget_dao / task_dao 的「预查 + 唯一约束兜底」模式：upsert 时先按唯一键
(user_id, period_type, period_value) 查，命中则更新 content/title/stats_summary
与 updated_at，未命中则插入新行；并发下漏过预查时由 uq_ai_report_scope 兜底，
经 translate_unique_violation 翻译为 ConflictError（与 category/task 一致）。
"""

import time
from typing import Optional

from sqlalchemy import select

from app.db.base import get_db, translate_unique_violation
from app.db.models import AIReport


def _now() -> float:
    return time.time()


class AIReportDAO:
    @staticmethod
    def list_all(user_id: str, period_type: Optional[str] = None) -> list[dict]:
        """某账号全部归档报告（不含 content/stats_summary），按周期类型升序、更新时间倒序

        period_type 过滤为可选，前端列表通常按类型分组展示。
        """
        with get_db() as session:
            stmt = select(AIReport).where(AIReport.user_id == user_id)
            if period_type:
                stmt = stmt.where(AIReport.period_type == period_type)
            stmt = stmt.order_by(
                AIReport.period_type.asc(), AIReport.updated_at.desc()
            )
            rows = session.scalars(stmt)
            return [r.as_list_dict() for r in rows]

    @staticmethod
    def get(user_id: str, report_id: int) -> Optional[dict]:
        """单条详情（含 content / stats_summary）；不存在或跨账号返回 None"""
        with get_db() as session:
            report = session.scalar(
                select(AIReport).where(
                    AIReport.id == report_id, AIReport.user_id == user_id
                )
            )
            return report.as_dict() if report is not None else None

    @staticmethod
    def get_by_scope(
        user_id: str, period_type: str, period_value: str
    ) -> Optional[dict]:
        """按唯一键查归档报告"""
        with get_db() as session:
            report = session.scalar(
                select(AIReport).where(
                    AIReport.user_id == user_id,
                    AIReport.period_type == period_type,
                    AIReport.period_value == period_value,
                )
            )
            return report.as_dict() if report is not None else None

    @staticmethod
    def upsert(
        user_id: str,
        period_type: str,
        period_value: str,
        title: str,
        content: str,
        stats_summary: str,
    ) -> dict:
        """按唯一键插入或更新归档报告；并发冲突经唯一约束兜底转 ConflictError

        created_at 仅在首次插入时写入；updated_at 每次归档都更新。
        flush 让唯一约束在上下文管理器内立即触发，避免 IntegrityError 推迟到
        commit、翻译上下文已退出（对齐 task_dao.ensure_task 写法）。
        """
        now = _now()
        with get_db() as session:
            report = session.scalar(
                select(AIReport).where(
                    AIReport.user_id == user_id,
                    AIReport.period_type == period_type,
                    AIReport.period_value == period_value,
                )
            )
            if report is not None:
                # 更新路径：预查命中，直接改字段，无并发竞争
                report.title = title
                report.content = content
                report.stats_summary = stats_summary
                report.updated_at = now
                session.flush()
                return report.as_dict()
            # 插入路径：并发下可能两请求同时到这里，唯一约束兜底翻译冲突
            with translate_unique_violation("该周期的归档报告正被并发写入，请重试"):
                report = AIReport(
                    user_id=user_id,
                    period_type=period_type,
                    period_value=period_value,
                    title=title,
                    content=content,
                    stats_summary=stats_summary,
                    created_at=now,
                    updated_at=now,
                )
                session.add(report)
                session.flush()
                return report.as_dict()

    @staticmethod
    def delete(user_id: str, report_id: int) -> bool:
        """删除归档报告（仅当前账号），返回是否存在"""
        with get_db() as session:
            report = session.scalar(
                select(AIReport).where(
                    AIReport.id == report_id, AIReport.user_id == user_id
                )
            )
            if report is None:
                return False
            session.delete(report)
        return True
