"""v5 → v6 schema 迁移测试：通知中心新表（notifications / notification_reads）

v6 迁移策略与 v3/v4/v5 一致：新表由 init_db 的 Base.metadata.create_all 按
方言幂等创建（notifications.event_key 唯一约束做事件去重），迁移函数本身只
推进版本戳。本测试模拟「v5 老库（无通知表）→ v6」的升级路径，验证：
    1. 迁移函数幂等（重复执行不报错）
    2. create_all 在迁移后补建两张通知表
    3. event_key 唯一约束生效（同一事件只能存一条）
    4. 迁移完成后 DAO 流程可用（create 去重 / 未读水位线 / 全部已读）
    5. 版本戳正确推进到 6
"""

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import DBSettings
from app.db.base import read_schema_version, set_schema_version
from app.db.engine import _STATE
from app.db.dao.notify_dao import NotificationDAO
from app.db.migrations import _v6_add_notification_tables
from app.db.models import Base, Notification
from tests.conftest import USER_A, make_engine


@pytest.fixture()
def v5_engine(tmp_path):
    """模拟 v5 老库：所有 v5 表已建好，但两张通知表不存在，schema_version=5"""
    engine = make_engine(tmp_path / "v5.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE notifications")
        conn.exec_driver_sql("DROP TABLE notification_reads")
    with Session(engine) as session:
        set_schema_version(session, 5)
        session.commit()
    yield engine
    engine.dispose()


def _migrate(engine: object) -> None:
    """跑迁移 + create_all 补建新表（复刻 init_db 的升级路径）"""
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 5
        _v6_add_notification_tables(session)
        set_schema_version(session, 6)
        session.commit()
    Base.metadata.create_all(engine)  # type: ignore[arg-type]
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 6


def test_v6_migration_is_idempotent(v5_engine):
    """迁移函数本身幂等（无列级变更，仅推进版本戳，可重复执行）"""
    with Session(v5_engine) as session:
        _v6_add_notification_tables(session)
        set_schema_version(session, 6)
        session.commit()
        _v6_add_notification_tables(session)


def test_v6_migration_creates_notification_tables(v5_engine):
    _migrate(v5_engine)

    inspector = inspect(v5_engine)
    tables = set(inspector.get_table_names())
    assert {"notifications", "notification_reads"} <= tables

    cols = {c["name"] for c in inspector.get_columns("notifications")}
    assert {
        "id",
        "user_id",
        "event_type",
        "event_key",
        "title",
        "content",
        "push_status",
        "push_error",
        "created_at",
    } <= cols
    assert {c["name"] for c in inspector.get_columns("notification_reads")} >= {
        "user_id",
        "last_read_id",
        "updated_at",
    }

    # event_key 唯一约束存在
    uniques = inspector.get_unique_constraints("notifications")
    assert any(set(u["column_names"]) == {"event_key"} for u in uniques)


def test_v6_migration_event_key_unique_enforced(v5_engine):
    _migrate(v5_engine)

    with Session(v5_engine) as session:
        session.add(
            Notification(
                user_id="",
                event_type="task_failed",
                event_key="task_failed:1",
                title="t",
                content="",
                created_at=1.0,
            )
        )
        session.commit()

    with pytest.raises(IntegrityError):
        with Session(v5_engine) as session:
            session.add(
                Notification(
                    user_id="",
                    event_type="task_failed",
                    event_key="task_failed:1",
                    title="t2",
                    content="",
                    created_at=2.0,
                )
            )
            session.commit()


def test_v6_migration_then_dao_flow(v5_engine):
    """迁移 + create_all 后激活引擎：通知 DAO 全流程可用"""
    _migrate(v5_engine)

    previous = _STATE.activate(DBSettings(db_type="sqlite"), v5_engine)
    if previous is not None:
        previous.dispose()

    first = NotificationDAO.create(
        user_id="",
        event_type="import_done",
        event_key="import_done:1",
        title="导入完成",
        content="新增 3 条",
    )
    assert first is not None
    # 去重
    assert (
        NotificationDAO.create(
            user_id="",
            event_type="import_done",
            event_key="import_done:1",
            title="导入完成",
            content="新增 3 条",
        )
        is None
    )

    # 未读水位线
    assert NotificationDAO.unread_count(USER_A) == 1
    NotificationDAO.mark_all_read(USER_A)
    assert NotificationDAO.unread_count(USER_A) == 0
