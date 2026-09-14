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
from app.db.models import AssetSnapshot, Bill, Budget, Category

_CHUNK = 500

# bills 缺失列的兜底值（源库可能是缺新列的旧版本）
_BILL_DEFAULTS = {"user_id": "", "tags": "", "reimbursed": False, "deleted": False}


def _table_count(session: Session, model, exists: bool) -> int:
    """表存在时返回行数，否则 0（源库可能是缺 categories 表的旧版本）"""
    if not exists:
        return 0
    return session.scalar(select(func.count()).select_from(model)) or 0


def _sync_pg_sequences(session: Session) -> None:
    """PG 的 SERIAL 序列不感知显式插入的 id，整库搬移后必须把序列拨到最大 id 之后

    必须在搬移同一事务内执行（setval 可随事务回滚）：若在提交后单独执行，
    进程在两步之间中断会让序列停在初始值，之后任何插入都撞主键且无法自愈。
    """
    if session.bind.dialect.name != "postgresql":
        return
    conn = session.connection()
    for table in ("bills", "categories", "budgets", "asset_snapshots"):
        seq = conn.execute(
            text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": table}
        ).scalar()
        if seq:
            conn.execute(
                text(
                    f"SELECT setval(:seq, COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
                ),
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


def _null_tx_keys(session: Session) -> set[tuple]:
    """目标库已有「无交易号」流水的业务键（合并模式下去重依据）

    tx_id 的唯一约束不去重 NULL（SQL 语义 NULL ≠ NULL），而人工记账不填
    交易号、部分解析器流水无单号都很常见 —— 这些行重复搬移会被反复插入、
    每次翻倍。业务键无法绝对精确（同日同商户同金额的两笔合法流水同键），
    因此只对「目标库已存在」的键跳过：单次搬移内的合法重复行全部保留，
    仅防止跨次重复膨胀。
    """
    rows = session.execute(
        select(
            Bill.user_id,
            Bill.tx_time,
            Bill.account,
            Bill.merchant,
            Bill.amount,
        ).where(Bill.tx_id.is_(None))
    )
    return set(rows)


def copy_database(
    source: Engine, target: Engine, schema_version: int = LATEST_SCHEMA_VERSION
) -> dict:
    """执行搬移，返回统计（源行数 / 实际复制行数 / 目标原本是否有数据）

    budgets 按 (user_id, month, category) 唯一键去重；asset_snapshots 无唯一键，
    合并模式下原样追加。源库缺新表（旧版本）时自动跳过。
    """
    src_inspect = inspect(source)
    src_has_cats = src_inspect.has_table("categories")
    src_has_budgets = src_inspect.has_table("budgets")
    src_has_assets = src_inspect.has_table("asset_snapshots")
    target_has_cats = inspect(target).has_table("categories")

    with Session(source) as src:
        source_bills = _table_count(src, Bill, True)
        source_categories = _table_count(src, Category, src_has_cats)
        source_budgets = _table_count(src, Budget, src_has_budgets)
        source_assets = _table_count(src, AssetSnapshot, src_has_assets)
        bill_rows = _stream_rows(src, source, Bill, defaults=_BILL_DEFAULTS)

        with Session(target) as tgt:
            before_bills = _table_count(tgt, Bill, True)
            before_cats = _table_count(tgt, Category, target_has_cats)
            before_budgets = _table_count(tgt, Budget, True)
            before_assets = _table_count(tgt, AssetSnapshot, True)
            target_had_data = before_bills > 0 or before_cats > 0
            preserve_ids = not target_had_data

            if src_has_cats:
                cat_rows = [
                    {"id": c.id, "name": c.name} if preserve_ids else {"name": c.name}
                    for c in src.scalars(select(Category))
                ]
                insert_ignore_rows(tgt.connection(), Category.__table__, cat_rows)

            # 合并模式：先取目标库已有无号流水的业务键，搬移时跳过这些键，
            # 防止重复执行「迁移并切换」时无交易号流水反复翻倍（见 _null_tx_keys）
            existing_null_keys = set() if preserve_ids else _null_tx_keys(tgt)

            buffer: list[dict] = []
            for row in bill_rows:
                if not preserve_ids:
                    row.pop("id", None)
                    if (
                        row.get("tx_id") is None
                        and (
                            row.get("user_id"),
                            row.get("tx_time"),
                            row.get("account"),
                            row.get("merchant"),
                            row.get("amount"),
                        )
                        in existing_null_keys
                    ):
                        continue
                buffer.append(row)
                if len(buffer) >= _CHUNK:
                    insert_ignore_rows(tgt.connection(), Bill.__table__, buffer)
                    buffer.clear()
            insert_ignore_rows(tgt.connection(), Bill.__table__, buffer)

            # 预算与资产快照：目标非空时按唯一键去重 / 追加（append 语义）
            if src_has_budgets:
                budget_rows = [
                    {k: v for k, v in r.items() if not (k == "id" and not preserve_ids)}
                    for r in (b.as_dict() for b in src.scalars(select(Budget)))
                ]
                insert_ignore_rows(tgt.connection(), Budget.__table__, budget_rows)
            if src_has_assets:
                asset_rows = [
                    {k: v for k, v in r.items() if not (k == "id" and not preserve_ids)}
                    for r in (a.as_dict() for a in src.scalars(select(AssetSnapshot)))
                ]
                insert_ignore_rows(
                    tgt.connection(), AssetSnapshot.__table__, asset_rows
                )

            tgt.flush()
            set_schema_version(tgt, schema_version)
            if preserve_ids:
                _sync_pg_sequences(tgt)
            tgt.commit()

    with Session(target) as tgt:
        copied_bills = _table_count(tgt, Bill, True) - before_bills
        copied_categories = _table_count(tgt, Category, target_has_cats) - before_cats
        copied_budgets = _table_count(tgt, Budget, True) - before_budgets
        copied_assets = _table_count(tgt, AssetSnapshot, True) - before_assets
    return {
        "source_bills": source_bills,
        "source_categories": source_categories,
        "source_budgets": source_budgets,
        "source_assets": source_assets,
        "copied_bills": copied_bills,
        "copied_categories": copied_categories,
        "copied_budgets": copied_budgets,
        "copied_assets": copied_assets,
        "target_had_data": target_had_data,
    }
