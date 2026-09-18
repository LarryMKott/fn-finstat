"""账本维度的数据层原语（T-7.1）：默认账本的创建与解析

为什么独立成模块：schema 迁移（app.db.migrations）也要创建/查默认账本，而
migrations 由 app.db.base 导入；若把这些原语放进依赖 base 的模块（DAO 层），
就会形成「base → migrations → dao → base」的循环导入。本模块只依赖 ORM 模型与
config，供迁移、DAO、服务三层共用。
"""

import time
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import DEFAULT_LEDGER_NAME
from app.db.models import DEFAULT_LEDGER_ID, Ledger


def ensure_default_ledger(session: Session) -> int:
    """确保默认账本存在（幂等），返回其 id

    两条创建路径共用本函数：
    - 老库升级：v8 迁移先把默认账本建出来，再用它的 id 作三张表 ledger_id 列的
      DDL 默认值，历史数据因此全部挂到默认账本上；
    - 全新安装：不走任何迁移（建表即最新版本），由 init_db 在建表后直接补建。

    识别顺序：先按 is_default 标记，再按默认账本名（老数据可能存在同名但缺标记的
    账本），都没有才新建。新建时 owner_id 为空串（应用级共享），与分类全局共享同口径。
    """
    existing = session.scalar(select(Ledger.id).where(Ledger.is_default.is_(True)))
    if existing is not None:
        return int(existing)
    by_name = session.scalar(
        select(Ledger.id).where(Ledger.name == DEFAULT_LEDGER_NAME)
    )
    if by_name is not None:
        session.execute(
            update(Ledger).where(Ledger.id == by_name).values(is_default=True)
        )
        session.flush()
        return int(by_name)
    now = time.time()
    ledger = Ledger(
        name=DEFAULT_LEDGER_NAME,
        owner_id="",
        is_default=True,
        remark="",
        created_at=now,
        updated_at=now,
    )
    session.add(ledger)
    session.flush()
    return int(ledger.id)


def default_ledger_id(session: Session) -> int:
    """当前默认账本 id；异常情况下回退到 DEFAULT_LEDGER_ID（列级默认值同值）"""
    try:
        return ensure_default_ledger(session)
    except Exception:  # pragma: no cover - 仅在库不可用时兜底，避免写路径整体失败
        return DEFAULT_LEDGER_ID


def resolve_ledger_id(session: Session, ledger_id: Optional[int]) -> int:
    """写路径的账本解析：未指定（None/0）时落到默认账本，指定则原样返回

    「不传 ledger_id 的旧调用行为不变」由本函数保证：升级前全库只有默认账本，
    旧调用等价于落在默认账本上；升级后依然如此。
    账本是否存在由调用方（服务层）校验，本函数只做空值折叠，
    避免每条写操作都多一次查询。
    """
    if ledger_id:
        return int(ledger_id)
    return default_ledger_id(session)
