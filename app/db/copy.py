"""跨数据库数据搬移：把源库的 bills/categories 复制到目标库

两条使用路径：
- 启动时数据库类型变更（旧 SQLite 文件 → 新库，见 base._handle_db_type_switch）
- 设置页「迁移并切换」（当前库 → 用户指定的 MySQL/PostgreSQL，见设置服务）

搬移规则：
- 目标为空：整库搬移并保留源 id 与账号归属（如回退到源库，自增 id 无缝衔接）
- 目标非空：按唯一键（流水 tx_id / 分类名）去重合并，id 由目标库自增
- 目标库 schema_version 统一记为 LATEST（目标表已按当前模型建表）
- 源库列取与当前模型的交集：允许源库是缺新列的旧版本（如无 user_id 的 v1 库）
- 账本维度（T-7.1）：ledgers 随数据一起搬移，流水/预算/快照的 ledger_id 按
  **账本名**重映射（目标库 id 与源库不一定相同）；映射不到或源库无账本表时
  落到目标库默认账本
- 家庭空间（T-7.2）：families / family_members 随数据一起搬移，成员行的
  family_id 按**邀请码**重映射（码全局唯一，可跨库对齐）；源库无家庭表或
  码映射不到时跳过对应成员行（不落孤儿成员）。家庭预算行（user_id 为
  family:N 合成属主）的 family_id / user_id 同步按邀请码重映射，映射不到
  跳过——否则预算会挂到目标库同 id 的无关家庭名下
- 借贷（T-7.5）/ 报销（T-7.4）/ 储蓄目标（T-1.4）：loans / loan_payments /
  reimbursements / savings_goals 随数据一起搬移。loan_payments.loan_id 与
  bills.reimb_id 按**业务键**（借条四元组 / 报销单三元组）重映射到目标库
  新 id，映射不到按孤儿跳过/置空（与备份恢复同语义）；三类实体无数据库
  唯一键，合并模式下按业务键去重，防止重复执行「迁移并切换」时翻倍
"""

from sqlalchemy import func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.constants import family_scope_user
from app.db.base import LATEST_SCHEMA_VERSION, insert_ignore_rows, set_schema_version
from app.db.ledgers import ensure_default_ledger
from app.db.models import (
    AssetSnapshot,
    Bill,
    Budget,
    Category,
    Family,
    FamilyMember,
    Ledger,
    Loan,
    LoanPayment,
    Reimbursement,
    SavingsGoal,
)

_CHUNK = 500

# bills 缺失列的兜底值（源库可能是缺新列的旧版本）
# ledger_id 不在其中：兜底值必须是「目标库默认账本 id」，随目标库而定，
# 由 copy_database 按目标库实际值注入（见 bill_defaults）
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
    for table in (
        "bills",
        "categories",
        "ledgers",
        "families",
        "family_members",
        "budgets",
        "asset_snapshots",
        "loans",
        "loan_payments",
        "reimbursements",
        "savings_goals",
    ):
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
    src_has_ledgers = src_inspect.has_table("ledgers")
    src_has_families = src_inspect.has_table("families")
    src_has_budgets = src_inspect.has_table("budgets")
    src_has_assets = src_inspect.has_table("asset_snapshots")
    src_has_loans = src_inspect.has_table("loans")
    src_has_payments = src_inspect.has_table("loan_payments")
    src_has_reimbs = src_inspect.has_table("reimbursements")
    src_has_savings = src_inspect.has_table("savings_goals")
    target_has_cats = inspect(target).has_table("categories")

    with Session(source) as src:
        source_bills = _table_count(src, Bill, True)
        source_categories = _table_count(src, Category, src_has_cats)
        source_budgets = _table_count(src, Budget, src_has_budgets)
        source_assets = _table_count(src, AssetSnapshot, src_has_assets)
        source_ledgers = _table_count(src, Ledger, src_has_ledgers)
        source_families = _table_count(src, Family, src_has_families)
        source_family_members = _table_count(src, FamilyMember, src_has_families)
        source_loans = _table_count(src, Loan, src_has_loans)
        source_loan_payments = _table_count(src, LoanPayment, src_has_payments)
        source_reimbursements = _table_count(src, Reimbursement, src_has_reimbs)
        source_savings_goals = _table_count(src, SavingsGoal, src_has_savings)
        # 源库账本 id → 账本名：目标库按名重新取 id（两库 id 不一定相同）
        ledger_id_to_name = (
            {l.id: l.name for l in src.scalars(select(Ledger))}
            if src_has_ledgers
            else {}
        )

        with Session(target) as tgt:
            before_bills = _table_count(tgt, Bill, True)
            before_cats = _table_count(tgt, Category, target_has_cats)
            before_budgets = _table_count(tgt, Budget, True)
            before_assets = _table_count(tgt, AssetSnapshot, True)
            before_ledgers = _table_count(tgt, Ledger, True)
            before_families = _table_count(tgt, Family, True)
            before_loans = _table_count(tgt, Loan, True)
            before_payments = _table_count(tgt, LoanPayment, True)
            before_reimbs = _table_count(tgt, Reimbursement, True)
            before_savings = _table_count(tgt, SavingsGoal, True)
            target_had_data = before_bills > 0 or before_cats > 0
            preserve_ids = not target_had_data

            # 账本先落库：流水/预算/快照的 ledger_id 要按名重映射到目标库 id
            if src_has_ledgers:
                ledger_rows = [
                    (
                        l.as_dict()
                        if preserve_ids
                        else {k: v for k, v in l.as_dict().items() if k != "id"}
                    )
                    for l in src.scalars(select(Ledger))
                ]
                insert_ignore_rows(tgt.connection(), Ledger.__table__, ledger_rows)
                tgt.flush()
            target_default = ensure_default_ledger(tgt)
            name_to_id = {row.name: row.id for row in tgt.scalars(select(Ledger))}
            ledger_map = {
                src_id: name_to_id.get(name, target_default)
                for src_id, name in ledger_id_to_name.items()
            }
            # 家庭先落库：成员行的 family_id 要按邀请码重映射到目标库 id
            if src_has_families:
                family_rows = [
                    (
                        f.as_dict()
                        if preserve_ids
                        else {k: v for k, v in f.as_dict().items() if k != "id"}
                    )
                    for f in src.scalars(select(Family))
                ]
                insert_ignore_rows(tgt.connection(), Family.__table__, family_rows)
                tgt.flush()
            code_to_id = {
                row.invite_code: row.id for row in tgt.scalars(select(Family))
            }
            family_map = (
                {
                    f.id: code_to_id.get(f.invite_code)
                    for f in src.scalars(select(Family))
                }
                if src_has_families
                else {}
            )
            # 借贷/报销先落库：流水的 reimb_id 与还款的 loan_id 要按业务键
            # 重映射到目标库新 id。两类实体无数据库唯一键，合并模式按业务键
            # 去重（同键跳过并映射到既有行），防止重复迁移时翻倍
            loan_map: dict[int, int | None] = {}
            if src_has_loans:
                existing_loan_keys = (
                    {}
                    if preserve_ids
                    else {
                        (l.user_id, l.direction, l.counterparty, l.created_at): l.id
                        for l in tgt.scalars(select(Loan))
                    }
                )
                loan_rows, loan_keyed = [], []
                for l in src.scalars(select(Loan)):
                    key = (l.user_id, l.direction, l.counterparty, l.created_at)
                    if not preserve_ids and key in existing_loan_keys:
                        loan_map[l.id] = existing_loan_keys[key]
                        continue
                    loan_rows.append(
                        l.as_dict()
                        if preserve_ids
                        else {k: v for k, v in l.as_dict().items() if k != "id"}
                    )
                    loan_keyed.append((l.id, key))
                insert_ignore_rows(tgt.connection(), Loan.__table__, loan_rows)
                tgt.flush()
                if preserve_ids:
                    loan_map = {old: old for old, _ in loan_keyed}
                else:
                    fresh_loans = {
                        (l.user_id, l.direction, l.counterparty, l.created_at): l.id
                        for l in tgt.scalars(select(Loan))
                    }
                    loan_map.update(
                        {old: fresh_loans.get(key) for old, key in loan_keyed}
                    )
            claim_map: dict[int, int | None] = {}
            if src_has_reimbs:
                existing_claim_keys = (
                    {}
                    if preserve_ids
                    else {
                        (r.user_id, r.title, r.created_at): r.id
                        for r in tgt.scalars(select(Reimbursement))
                    }
                )
                claim_rows, claim_keyed = [], []
                for r in src.scalars(select(Reimbursement)):
                    key = (r.user_id, r.title, r.created_at)
                    if not preserve_ids and key in existing_claim_keys:
                        claim_map[r.id] = existing_claim_keys[key]
                        continue
                    claim_rows.append(
                        r.as_dict()
                        if preserve_ids
                        else {k: v for k, v in r.as_dict().items() if k != "id"}
                    )
                    claim_keyed.append((r.id, key))
                insert_ignore_rows(
                    tgt.connection(), Reimbursement.__table__, claim_rows
                )
                tgt.flush()
                if preserve_ids:
                    claim_map = {old: old for old, _ in claim_keyed}
                else:
                    fresh_claims = {
                        (r.user_id, r.title, r.created_at): r.id
                        for r in tgt.scalars(select(Reimbursement))
                    }
                    claim_map.update(
                        {old: fresh_claims.get(key) for old, key in claim_keyed}
                    )
            bill_defaults = {**_BILL_DEFAULTS, "ledger_id": target_default}
            bill_rows = _stream_rows(src, source, Bill, defaults=bill_defaults)

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
                row["ledger_id"] = ledger_map.get(row.get("ledger_id"), target_default)
                if not preserve_ids and row.get("reimb_id") is not None:
                    # 报销单 id 已按业务键重映射；映射不到（重复去重/孤儿）置空，
                    # 与备份恢复同语义：reimbursed 标记保留原值，仅解除关联
                    row["reimb_id"] = claim_map.get(row["reimb_id"])
                buffer.append(row)
                if len(buffer) >= _CHUNK:
                    insert_ignore_rows(tgt.connection(), Bill.__table__, buffer)
                    buffer.clear()
            insert_ignore_rows(tgt.connection(), Bill.__table__, buffer)

            # 预算与资产快照：目标非空时按唯一键去重 / 追加（append 语义）。
            # 家庭预算行（user_id=family:N）的 family_id 按邀请码重映射到目标库
            # id、user_id 同步改写为新 id 的合成属主；映射不到跳过（防挂错家）。
            # 整库搬移（目标空）时家庭 id 原样保留，无需改写
            if src_has_budgets:
                budget_rows = []
                for b in src.scalars(select(Budget)):
                    row = b.as_dict()
                    row["ledger_id"] = ledger_map.get(
                        row.get("ledger_id"), target_default
                    )
                    if not preserve_ids and row.get("family_id") is not None:
                        target_family = family_map.get(row["family_id"])
                        if target_family is None:
                            continue
                        row["family_id"] = target_family
                        row["user_id"] = family_scope_user(target_family)
                    budget_rows.append(
                        row
                        if preserve_ids
                        else {k: v for k, v in row.items() if k != "id"}
                    )
                insert_ignore_rows(tgt.connection(), Budget.__table__, budget_rows)
            if src_has_assets:
                asset_rows = []
                for a in src.scalars(select(AssetSnapshot)):
                    row = a.as_dict()
                    row["ledger_id"] = ledger_map.get(
                        row.get("ledger_id"), target_default
                    )
                    asset_rows.append(
                        row
                        if preserve_ids
                        else {k: v for k, v in row.items() if k != "id"}
                    )
                insert_ignore_rows(
                    tgt.connection(), AssetSnapshot.__table__, asset_rows
                )
            if src_has_families:
                member_rows = []
                for m in src.scalars(select(FamilyMember)):
                    target_id = family_map.get(m.family_id)
                    if target_id is None:
                        continue  # 码在目标库重映射不到：跳过，不落孤儿成员
                    row = m.as_dict()
                    row["family_id"] = target_id
                    member_rows.append(
                        row
                        if preserve_ids
                        else {k: v for k, v in row.items() if k != "id"}
                    )
                insert_ignore_rows(
                    tgt.connection(), FamilyMember.__table__, member_rows
                )
            # 还款明细：loan_id 按映射重写，映射不到（借条被去重/源库不一致）
            # 按孤儿跳过；合并模式按 (目标借条, 金额, 日期, 创建时刻) 去重
            if src_has_payments:
                existing_payment_keys = (
                    set()
                    if preserve_ids
                    else {
                        (p.loan_id, p.amount, p.pay_date, p.created_at)
                        for p in tgt.scalars(select(LoanPayment))
                    }
                )
                payment_rows = []
                for p in src.scalars(select(LoanPayment)):
                    target_loan = loan_map.get(p.loan_id)
                    if target_loan is None:
                        continue
                    key = (target_loan, p.amount, p.pay_date, p.created_at)
                    if not preserve_ids and key in existing_payment_keys:
                        continue
                    row = p.as_dict()
                    row["loan_id"] = target_loan
                    payment_rows.append(
                        row
                        if preserve_ids
                        else {k: v for k, v in row.items() if k != "id"}
                    )
                insert_ignore_rows(
                    tgt.connection(), LoanPayment.__table__, payment_rows
                )
            # 储蓄目标：无 id 引用方，直搬；合并模式按业务键去重防翻倍
            if src_has_savings:
                existing_goal_keys = (
                    set()
                    if preserve_ids
                    else {
                        (g.user_id, g.name, g.start_date, g.created_at)
                        for g in tgt.scalars(select(SavingsGoal))
                    }
                )
                goal_rows = []
                for g in src.scalars(select(SavingsGoal)):
                    if (
                        not preserve_ids
                        and (
                            g.user_id,
                            g.name,
                            g.start_date,
                            g.created_at,
                        )
                        in existing_goal_keys
                    ):
                        continue
                    goal_rows.append(
                        g.as_dict()
                        if preserve_ids
                        else {k: v for k, v in g.as_dict().items() if k != "id"}
                    )
                insert_ignore_rows(tgt.connection(), SavingsGoal.__table__, goal_rows)

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
        copied_ledgers = _table_count(tgt, Ledger, True) - before_ledgers
        copied_families = _table_count(tgt, Family, True) - before_families
        copied_loans = _table_count(tgt, Loan, True) - before_loans
        copied_loan_payments = _table_count(tgt, LoanPayment, True) - before_payments
        copied_reimbursements = _table_count(tgt, Reimbursement, True) - before_reimbs
        copied_savings_goals = _table_count(tgt, SavingsGoal, True) - before_savings
    return {
        "source_bills": source_bills,
        "source_categories": source_categories,
        "source_ledgers": source_ledgers,
        "source_families": source_families,
        "source_family_members": source_family_members,
        "source_budgets": source_budgets,
        "source_assets": source_assets,
        "source_loans": source_loans,
        "source_loan_payments": source_loan_payments,
        "source_reimbursements": source_reimbursements,
        "source_savings_goals": source_savings_goals,
        "copied_bills": copied_bills,
        "copied_categories": copied_categories,
        "copied_ledgers": copied_ledgers,
        "copied_families": copied_families,
        "copied_budgets": copied_budgets,
        "copied_assets": copied_assets,
        "copied_loans": copied_loans,
        "copied_loan_payments": copied_loan_payments,
        "copied_reimbursements": copied_reimbursements,
        "copied_savings_goals": copied_savings_goals,
        "target_had_data": target_had_data,
    }
