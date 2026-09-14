"""v4 → v5 schema 迁移测试：ai_reports 新表与唯一约束

v5 迁移策略与 v3/v4 一致：新表由 init_db 的 Base.metadata.create_all 按
方言幂等创建（含 uq_ai_report_scope 唯一约束），迁移函数本身只推进版本戳。
本测试模拟「v4 老库（无 ai_reports 表）→ v5」的升级路径，验证：
    1. 迁移函数幂等（重复执行不报错）
    2. create_all 在迁移后补建 ai_reports 表
    3. 唯一约束生效（同 user_id + period_type + period_value 只能存一条）
    4. 迁移完成后 DAO 流程可用（archive_report upsert / list / get / delete）
    5. 版本戳正确推进到 5
"""

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import DBSettings
from app.db.base import _STATE, read_schema_version, set_schema_version
from app.db.dao.ai_report_dao import AIReportDAO
from app.db.migrations import _v5_add_ai_reports_table
from app.db.models import AIReport, Base
from tests.conftest import USER_A, make_engine


@pytest.fixture()
def v4_engine(tmp_path):
    """模拟 v4 老库：所有 v4 表已建好，但 ai_reports 表不存在，schema_version=4

    策略：先用 Base.metadata.create_all 建出所有表（包含 ai_reports），
    再手工 DROP ai_reports 模拟 v4 状态；version 戳写 4。
    """
    engine = make_engine(tmp_path / "v4.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE ai_reports")
    with Session(engine) as session:
        set_schema_version(session, 4)
        session.commit()
    yield engine
    engine.dispose()


def test_v5_migration_is_idempotent(v4_engine):
    """迁移函数本身幂等（无列级变更，仅推进版本戳，可重复执行）"""
    with Session(v4_engine) as session:
        assert read_schema_version(session) == 4
        _v5_add_ai_reports_table(session)
        set_schema_version(session, 5)
        session.commit()
        assert read_schema_version(session) == 5

        # 幂等：重复执行不报错
        _v5_add_ai_reports_table(session)


def test_v5_migration_creates_ai_reports_table(v4_engine):
    """迁移 + create_all 后 ai_reports 表存在，结构与模型一致"""
    with Session(v4_engine) as session:
        _v5_add_ai_reports_table(session)
        set_schema_version(session, 5)
        session.commit()
    # init_db 在迁移后调用 create_all 补建新表，这里复刻该路径
    Base.metadata.create_all(v4_engine)

    inspector = inspect(v4_engine)
    assert "ai_reports" in inspector.get_table_names()

    cols = {c["name"] for c in inspector.get_columns("ai_reports")}
    assert {
        "id",
        "user_id",
        "period_type",
        "period_value",
        "title",
        "content",
        "stats_summary",
        "created_at",
        "updated_at",
    } <= cols

    # 唯一约束存在
    uniques = inspector.get_unique_constraints("ai_reports")
    assert any(
        set(u["column_names"]) == {"user_id", "period_type", "period_value"}
        for u in uniques
    )


def test_v5_migration_unique_constraint_enforced(v4_engine):
    """迁移后唯一约束生效：同 user_id + period_type + period_value 重复插入抛异常"""
    with Session(v4_engine) as session:
        _v5_add_ai_reports_table(session)
        set_schema_version(session, 5)
        session.commit()
    Base.metadata.create_all(v4_engine)

    with Session(v4_engine) as session:
        first = AIReport(
            user_id=USER_A,
            period_type="quarter",
            period_value="2026-Q3",
            title="报告 1",
            content="正文",
            stats_summary="",
            created_at=1.0,
            updated_at=1.0,
        )
        session.add(first)
        session.commit()

    # 直接插入同唯一键 → 触发唯一约束（不走 DAO upsert 路径，验证约束本身）
    with pytest.raises(IntegrityError):
        with Session(v4_engine) as session:
            session.add(
                AIReport(
                    user_id=USER_A,
                    period_type="quarter",
                    period_value="2026-Q3",
                    title="报告 2",
                    content="另一个正文",
                    stats_summary="",
                    created_at=2.0,
                    updated_at=2.0,
                )
            )
            session.commit()


def test_v5_migration_then_dao_flow(v4_engine):
    """迁移 + create_all 后激活引擎：AIReportDAO upsert/list/get/delete 可用"""
    with Session(v4_engine) as session:
        _v5_add_ai_reports_table(session)
        set_schema_version(session, 5)
        session.commit()
    Base.metadata.create_all(v4_engine)

    # 激活引擎，让 DAO 经 get_db() 走到这个 v5 库
    previous = _STATE.activate(DBSettings(db_type="sqlite"), v4_engine)
    if previous is not None:
        previous.dispose()

    # upsert 首次
    saved = AIReportDAO.upsert(
        user_id=USER_A,
        period_type="year",
        period_value="2026",
        title="2026 年度报告",
        content="# 年度报告",
        stats_summary='{"this_expense": 1000}',
    )
    assert saved["id"] > 0
    assert saved["period_value"] == "2026"

    # upsert 再次覆盖
    updated = AIReportDAO.upsert(
        user_id=USER_A,
        period_type="year",
        period_value="2026",
        title="2026 年度报告（更新）",
        content="# 年度报告 v2",
        stats_summary='{"this_expense": 2000}',
    )
    assert updated["id"] == saved["id"]
    assert updated["title"].endswith("（更新）")

    # list
    items = AIReportDAO.list_all(USER_A)
    assert len(items) == 1
    assert items[0]["id"] == saved["id"]

    # get
    detail = AIReportDAO.get(USER_A, saved["id"])
    assert detail["content"] == "# 年度报告 v2"

    # 跨账号隔离
    assert AIReportDAO.get("10002", saved["id"]) is None

    # delete
    assert AIReportDAO.delete(USER_A, saved["id"]) is True
    assert AIReportDAO.delete(USER_A, saved["id"]) is False
    assert AIReportDAO.list_all(USER_A) == []
