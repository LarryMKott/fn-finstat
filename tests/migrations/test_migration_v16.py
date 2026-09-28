"""v15 → v16 schema 迁移测试：分类层级三列 + category_keywords 播种

重点覆盖方案 §12 的两条底线：
- 老库（无 parent_id 列的 v15 形态 categories）升级后行为等价
- 内置词播种幂等（app_meta.keywords_seeded 守卫），只播一次
"""

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.config import DBSettings
from app.db.base import read_schema_version, set_schema_version
from app.db.engine import _STATE
from app.db.keyword_seed import KEYWORDS_SEEDED_KEY, keywords_seeded
from app.db.migrations import _v16_add_category_extension
from app.db.models import AppMeta, Base
from app.services import keyword_service
from app.utils.category_matcher import RULES
from tests.conftest import make_engine


@pytest.fixture()
def v15_engine(tmp_path):
    """v15 形态的库：categories 只有 id/name 两列，无 category_keywords 表"""
    engine = make_engine(tmp_path / "v15.db")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE categories")
        conn.exec_driver_sql(
            "CREATE TABLE categories ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name VARCHAR(64) NOT NULL UNIQUE)"
        )
        conn.exec_driver_sql(
            "INSERT INTO categories (name) VALUES ('餐饮'), ('交通'), ('其他')"
        )
        conn.exec_driver_sql("DROP TABLE category_keywords")
    with Session(engine) as session:
        set_schema_version(session, 15)
        session.commit()
    yield engine
    engine.dispose()


def _migrate(engine) -> None:
    # 与 init_db 同序：create_all（幂等建 category_keywords 新表）在迁移之前
    Base.metadata.create_all(engine)  # type: ignore[arg-type]
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 15
        _v16_add_category_extension(session)
        set_schema_version(session, 16)
        session.commit()
    with Session(engine) as session:  # type: ignore[arg-type]
        assert read_schema_version(session) == 16


def test_v16_migration_adds_columns_and_seeds(v15_engine):
    _migrate(v15_engine)
    columns = {c["name"] for c in inspect(v15_engine).get_columns("categories")}
    assert {"parent_id", "source", "created_at"} <= columns
    assert "category_keywords" in inspect(v15_engine).get_table_names()

    with Session(v15_engine) as session:
        assert keywords_seeded(session) is True
        # 播种词与 RULES 逐字一致（仅限库内存在的分类）
        rows = session.execute(
            text(
                "SELECT c.name, k.keyword FROM category_keywords k "
                "JOIN categories c ON c.id = k.category_id "
                "WHERE k.source = 'builtin'"
            )
        ).all()
    seeded = {}
    for name, word in rows:
        seeded.setdefault(name, set()).add(word)
    assert seeded["餐饮"] == set(RULES["餐饮"])
    assert "宠物" not in seeded  # 该老库没有宠物分类，对应词组跳过
    # source/created_at 列默认值正确
    with Session(v15_engine) as session:
        name, source = session.execute(
            text("SELECT name, source FROM categories WHERE name = '餐饮'")
        ).one()
    assert (name, source) == ("餐饮", "manual")


def test_v16_migration_seeding_is_idempotent(v15_engine):
    _migrate(v15_engine)
    with Session(v15_engine) as session:
        first = session.execute(text("SELECT COUNT(*) FROM category_keywords")).scalar()
        _v16_add_category_extension(session)  # 守卫位已置：不重复播
        second = session.execute(
            text("SELECT COUNT(*) FROM category_keywords")
        ).scalar()
    assert first == second
    assert first >= len(RULES["餐饮"]) + len(RULES["交通"])


def test_v16_migration_then_matching(v15_engine):
    """升级后的库走新匹配链：内置词从表里命中（RULES 已退位为 seed）"""
    _migrate(v15_engine)
    previous = _STATE.activate(DBSettings(db_type="sqlite"), v15_engine)
    if previous is not None:
        previous.dispose()
    assert keyword_service.match("瑞幸咖啡（朝阳门店）") == "餐饮"
    assert keyword_service.match("完全未知的商户") == "其他"


def test_v16_guard_row_present(v15_engine):
    _migrate(v15_engine)
    with Session(v15_engine) as session:
        value = session.scalar(
            select(AppMeta.meta_value).where(AppMeta.meta_key == KEYWORDS_SEEDED_KEY)
        )
    assert value == "1"
