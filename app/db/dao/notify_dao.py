"""通知中心数据访问层：通知写入（事件去重）与按账号的未读/已读水位线

可见性模型（T-5.4，方案 B 应用内通知中心）：
- user_id 为空串 = 应用级广播（任务失败 / 自动导入完成等全局事件），
  对所有账号可见；具体账号 = 仅该账号可见（预算/报告等账号事件）；
- 未读数 = 「id 大于该账号已读水位线且对其可见」的行数（见
  NotificationRead 模型注释），「全部已读」把水位线推到当前最大 id。
"""

import time
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.base import get_db, is_unique_violation
from app.db.models import Notification, NotificationRead

# 通知保留窗口：通知是即时提醒而非归档，超窗顺手清理防无限增长
# （清理挂在 create 上，与 TaskDAO.add_run 的历史清理同策略）
NOTIFICATION_RETENTION_SECONDS = 90 * 24 * 60 * 60
# 列表单次返回上限：通知中心一次展示的条数（更早的历史随保留窗口淘汰）
LIST_LIMIT_MAX = 100


class NotificationDAO:
    @staticmethod
    def create(
        *,
        user_id: str,
        event_type: str,
        event_key: str,
        title: str,
        content: str,
        created_at: Optional[float] = None,
    ) -> Optional[dict]:
        """写入一条通知（event_key 唯一约束去重）；重复事件返回 None

        去重兜底靠唯一约束而非先查后插：并发的两个任务线程同时产同一事件
        （调度补跑与手动触发撞车）时，先查后插仍可能双双通过。
        """
        now = time.time() if created_at is None else created_at
        try:
            with get_db() as session:
                notification = Notification(
                    user_id=user_id,
                    event_type=event_type,
                    event_key=event_key,
                    title=title[:128],
                    content=content[:500],
                    created_at=now,
                )
                session.add(notification)
                session.flush()
        except IntegrityError as exc:
            if not is_unique_violation(exc):
                raise
            return None
        # 保留窗口外的旧通知顺手清理：写入频率低（天级/周级），无需独立清理任务
        with get_db() as session:
            session.execute(
                delete(Notification).where(
                    Notification.created_at
                    < time.time() - NOTIFICATION_RETENTION_SECONDS
                )
            )
        return notification.as_dict()

    @staticmethod
    def set_push_result(notification_id: int, ok: bool, error: str = "") -> None:
        """回写出站 Webhook 投递结果（失败原因落库供追溯；永不抛出调用方异常）"""
        with get_db() as session:
            session.execute(
                update(Notification)
                .where(Notification.id == notification_id)
                .values(
                    push_status="ok" if ok else "failed",
                    push_error="" if ok else error[:255],
                )
            )

    @staticmethod
    def latest_id() -> int:
        """当前最大通知 id（「全部已读」的目标水位线）"""
        with get_db() as session:
            return int(session.scalar(select(func.max(Notification.id))) or 0)

    @staticmethod
    def list_for_user(user_id: str, limit: int = 50) -> list[dict]:
        """某账号可见的通知（广播 + 定向），按 id 倒序"""
        limit = max(1, min(limit, LIST_LIMIT_MAX))
        with get_db() as session:
            stmt = (
                select(Notification)
                .where((Notification.user_id == "") | (Notification.user_id == user_id))
                .order_by(Notification.id.desc())
                .limit(limit)
            )
            return [n.as_dict() for n in session.scalars(stmt)]

    @staticmethod
    def get_read_watermark(user_id: str) -> int:
        with get_db() as session:
            return int(
                session.scalar(
                    select(NotificationRead.last_read_id).where(
                        NotificationRead.user_id == user_id
                    )
                )
                or 0
            )

    @staticmethod
    def unread_count(user_id: str) -> int:
        """未读数：可见且 id 大于该账号已读水位线"""
        watermark = NotificationDAO.get_read_watermark(user_id)
        with get_db() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(Notification)
                    .where(
                        Notification.id > watermark,
                        (Notification.user_id == "")
                        | (Notification.user_id == user_id),
                    )
                )
                or 0
            )

    @staticmethod
    def mark_all_read(user_id: str, last_id: Optional[int] = None) -> int:
        """推送已读水位线到 last_id（缺省为当前最大通知 id），返回新水位线

        upsert 写法兼容三方言（MySQL 需 ON DUPLICATE KEY，SQLite/PG 需
        ON CONFLICT，各自语法不同），改为「先改后插、冲突归零」的幂等序：
        已有水位线行时 UPDATE 必然命中，首次则 INSERT。
        """
        if last_id is None:
            last_id = NotificationDAO.latest_id()
        now = time.time()
        with get_db() as session:
            rowcount = session.execute(
                update(NotificationRead)
                .where(NotificationRead.user_id == user_id)
                .values(last_read_id=last_id, updated_at=now)
            ).rowcount
            if rowcount == 0:
                try:
                    session.add(
                        NotificationRead(
                            user_id=user_id, last_read_id=last_id, updated_at=now
                        )
                    )
                    session.flush()
                except IntegrityError as exc:
                    if not is_unique_violation(exc):
                        raise
                    # 并发首读：他方已建行，本方以相同 last_id 覆盖即可
                    session.execute(
                        update(NotificationRead)
                        .where(NotificationRead.user_id == user_id)
                        .values(last_read_id=last_id, updated_at=now)
                    )
        return last_id
