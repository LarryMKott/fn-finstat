"""跨数据库数据搬移：把源库的 bills/categories 复制到目标库

两条使用路径：
- 启动时数据库类型变更（旧 SQLite 文件 → 新库，见 base._handle_db_type_switch）
- 设置页「迁移并切换」（当前库 → 用户指定的 MySQL/PostgreSQL，见设置服务）

搬移规则：
- 目标为空：整库搬移并保留源 id 与账号归属（如回退到源库，自增 id 无缝衔接）
- 目标非空：按唯一键（流水 tx_id / 分类名）去重合并，id 由目标库自增
- 目标库 schema_version 统一记为 LATEST（目标表已按当前模型建表）
- 源库列取与当前模型的交集：允许源库是缺新列的旧版本（如无 user_id 的 v1 库）
"""
from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.base import LATEST_SCHEMA_VERSION, insert_ignore_rows, set_schema_version
from app.db.models import Bill, Category

_CHUNK = 500


def _table_count(session: Session, model, exists: bool) -> int:
    if not exists:
        return 0
    return session.scalar(select(func.count()).select_from(model)) or 0


def _sync_pg_sequences(target: Engine) -> None:
    """PG 的 SERIAL 序列不感知显式插入的 id，整库搬移后必须把序列拨到最大 id 之后"""
    if target.dialect.name != "postgresql":
        return
    with target.begin() as conn:
        for table in ("bills", "categories"):
            seq = conn.execute(
                text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": table}
            ).scalar()
            if seq:
                conn.execute(
                    text(f"SELECT setval(:seq, COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"),
                    {"seq": seq},
                )


def _stream_rows(src: Session, engine: Engine, model, defaults: dict[str, str]):
    """按源库实际存在的列分块读取（列取交集），逐行产出补齐默认值后的字典

    defaults：源库缺失列的默认值（当前：旧库无 user_id → 归入默认账号空串）。
    """
    src_cols = {c["name"] for c in inspect(engine).get_columns(model.__tablename__)}
    columns = [c for c in model.__table__.columns if c.name in src_cols]
    result = src.execute(select(*columns).execution_options(yield_per=_CHUNK))
    for row in result.mappings():
        row_dict = dict(row)
        for name, value in defaults.items():
            row_dict.setdefault(name, value)
        yield row_dict


def copy_database(source: Engine, target: Engine, schema_version: int = LATEST_SCHEMA_VERSION) -> dict:
    """执行搬移，返回统计（源行数 / 实际复制行数 / 目标原本是否有数据）"""
    src_has_cats = inspect(source).has_table("categories")
    target_has_cats = inspect(target).has_table("categories")

    with Session(source) as src:
        source_bills = _table_count(src, Bill, True)
        source_categories = _table_count(src, Category, src_has_cats)
        bill_rows = _stream_rows(src, source, Bill, defaults={"user_id": ""})

        with Session(target) as tgt:
            before_bills = _table_count(tgt, Bill, True)
            before_cats = _table_count(tgt, Category, target_has_cats)
            target_had_data = before_bills > 0 or before_cats > 0
            preserve_ids = not target_had_data

            if src_has_cats:
                cat_rows = [
                    {"id": c.id, "name": c.name} if preserve_ids else {"name": c.name}
                    for c in src.scalars(select(Category))
                ]
                insert_ignore_rows(tgt.connection(), Category.__table__, cat_rows)

            buffer: list[dict] = []
            for row in bill_rows:
                if not preserve_ids:
                    row.pop("id", None)
                buffer.append(row)
                if len(buffer) >= _CHUNK:
                    insert_ignore_rows(tgt.connection(), Bill.__table__, buffer)
                    buffer.clear()
            insert_ignore_rows(tgt.connection(), Bill.__table__, buffer)

            tgt.flush()
            set_schema_version(tgt, schema_version)
            tgt.commit()

    if preserve_ids:
        _sync_pg_sequences(target)
    with Session(target) as tgt:
        copied_bills = _table_count(tgt, Bill, True) - before_bills
        copied_categories = _table_count(tgt, Category, target_has_cats) - before_cats
    return {
        "source_bills": source_bills,
        "source_categories": source_categories,
        "copied_bills": copied_bills,
        "copied_categories": copied_categories,
        "target_had_data": target_had_data,
    }
