"""v8 → v9 schema 迁移测试：家庭空间新表（families / family_members）

v9 迁移策略与 v4~v7 一致：新表由 init_db 的 Base.metadata.create_all 按
方言幂等创建（family_members.user_id 全局唯一 = 一个账号至多加入一个家庭），
迁移函数本身只推进版本戳。本测试模拟「v8 老库（无家庭表）→ v9」升级路径：
    1. 迁移函数幂等（重复执行不报错）
    2. create_all 在迁移后补建两张家庭表
    3. user_id 唯一约束生效（重复加入被拒）
    4. 迁移完成后 DAO 流程可用（建家庭 / 凭码加入 / 成员列表）
    5. 版本戳正确推进到 9
"""

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.errors import ConflictError

from app.config import DBSettings
from app.db.base import read_schema_version, set_schema_version
from app.db.dao.family_dao import FamilyDAO
from app.db.engine import _STATE
from app.db.migrations import _v9_add_family_tables
from app.db.models import Base
from tests.conftest import make_engine


@pytest.fixture()
def v8_engine(tmp_path):
    """模拟 v8 老库：账本维度已就位，但家庭表不存在，schema_version=8"""
    engine = make_engine(tmp_path / "v8.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE family_members")
        conn.exec_driver_sql("DROP TABLE families")
    with Session(engine) as session:
        set_schema_version(session, 8)
        session.commit()
    yield engine
    engine.dispose()


def _migrate(engine: object) -> None:
    """跑迁移 + create_all 补建新表（复刻 init_db 的升级路径）"""
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 8
        _v9_add_family_tables(session)
        set_schema_version(session, 9)
        session.commit()
    Base.metadata.create_all(engine)  # type: ignore[arg-type]
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 9


def test_v9_migration_is_idempotent(v8_engine):
    with Session(v8_engine) as session:
        _v9_add_family_tables(session)
        set_schema_version(session, 9)
        session.commit()
        _v9_add_family_tables(session)


def test_v9_migration_creates_family_tables(v8_engine):
    _migrate(v8_engine)

    inspector = inspect(v8_engine)
    tables = set(inspector.get_table_names())
    assert {"families", "family_members"} <= tables
    family_cols = {c["name"] for c in inspector.get_columns("families")}
    assert {
        "id",
        "name",
        "invite_code",
        "allow_detail_view",
        "created_by",
    } <= family_cols
    member_cols = {c["name"] for c in inspector.get_columns("family_members")}
    assert {
        "id",
        "family_id",
        "user_id",
        "role",
        "nickname",
        "joined_at",
    } <= member_cols
    # user_id 全局唯一：一个账号至多加入一个家庭
    uniques = inspector.get_unique_constraints("family_members")
    assert any(set(u["column_names"]) == {"user_id"} for u in uniques)


def test_v9_migration_user_id_unique_enforced(v8_engine):
    _migrate(v8_engine)

    previous = _STATE.activate(DBSettings(db_type="sqlite"), v8_engine)
    if previous is not None:
        previous.dispose()

    family = FamilyDAO.create("唯一约束家庭", "u1")
    FamilyDAO.join(family["id"], "u2")

    # 唯一约束兜底并发加入： IntegrityError 被 translate_unique_violation 转业务冲突
    with pytest.raises(ConflictError):
        FamilyDAO.join(family["id"], "u2")  # 同账号二次加入


def test_v9_migration_then_dao_flow(v8_engine):
    """迁移 + create_all 后激活引擎：家庭 DAO 全流程可用"""
    _migrate(v8_engine)

    previous = _STATE.activate(DBSettings(db_type="sqlite"), v8_engine)
    if previous is not None:
        previous.dispose()

    family = FamilyDAO.create("我的家", "u1", nickname="张三")
    assert family["invite_code"]
    assert FamilyDAO.count_members(family["id"]) == 1

    FamilyDAO.join(family["id"], "u2", nickname="李四")
    members = FamilyDAO.list_members(family["id"])
    assert [m["role"] for m in members] == ["admin", "member"]  # 管理员排最前
    assert FamilyDAO.member_of("u2")["family_id"] == family["id"]

    assert FamilyDAO.regenerate_invite_code(family["id"]) != family["invite_code"]
    assert FamilyDAO.disband(family["id"]) is True
    assert FamilyDAO.member_of("u1") is None
