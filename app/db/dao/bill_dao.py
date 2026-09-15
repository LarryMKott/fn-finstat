"""账单流水数据访问层（SQLAlchemy ORM，数据按 user_id 归属飞牛账号）"""

from typing import Optional

from sqlalchemy import delete, func, select, update

from app.config import DEFAULT_CATEGORY
from app.core.errors import ErrorCode
from app.db.base import (
    get_db,
    in_chunks,
    insert_ignore_rows,
    translate_unique_violation,
)
from app.db.models import Bill
from app.utils.filters import build_criteria

# 允许排序的字段（映射到模型列再进 ORDER BY，防止注入）；服务层引用同一份做入参校验
SORTABLE_FIELDS = {
    "tx_time",
    "account",
    "tx_type",
    "merchant",
    "amount",
    "category",
    "remark",
}

# 单次导出/批量操作的条数上限（防止一次请求拖垮服务）
EXPORT_LIMIT = 100_000
BATCH_LIMIT = 1000


def _normalize(rec: dict, user_id: str) -> dict:
    """归一化记录：补归属账号；空交易号转 NULL（UNIQUE 允许多个 NULL，空串全局只允许一条）"""
    values = dict(rec)
    values["user_id"] = user_id
    if not values.get("tx_id"):
        values["tx_id"] = None
    return values


def _paged(conds: list, page: int, page_size: int, order_by) -> tuple[int, list[dict]]:
    """通用分页查询：同一条件下先 COUNT 再取当页，返回 (总数, 字典列表)"""
    with get_db() as session:
        total = session.scalar(select(func.count()).select_from(Bill).where(*conds))
        stmt = (
            select(Bill)
            .where(*conds)
            .order_by(*order_by)
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        rows = [b.as_dict() for b in session.scalars(stmt)]
    return total, rows


class BillDAO:
    @staticmethod
    def insert_many(records: list[dict], user_id: str) -> int:
        """批量插入（归属指定账号），交易号(tx_id)唯一去重；返回实际新增条数

        驱动 rowcount 不可靠（SQLite/PG 可能为 -1），统一用事务内 COUNT 差值：
        整个操作在单事务内完成，COUNT 差值在事务隔离下并发安全。
        """
        rows = [_normalize(r, user_id) for r in records]
        with get_db() as session:
            before = session.scalar(select(func.count()).select_from(Bill))
            insert_ignore_rows(session.connection(), Bill.__table__, rows)
            session.flush()
            after = session.scalar(select(func.count()).select_from(Bill))
        return max(0, after - before)

    @staticmethod
    def list_bills(
        user_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
        tx_type: Optional[str] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        reimbursed: Optional[bool] = None,
        include_deleted: bool = False,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "tx_time",
        order: str = "desc",
    ) -> tuple[int, list[dict]]:
        """多条件分页查询（仅当前账号），支持指定字段排序（字段经服务层白名单校验）"""
        conds = build_criteria(
            start,
            end,
            account,
            tx_type,
            category,
            user_id=user_id,
            include_deleted=include_deleted,
            tag=tag,
            reimbursed=reimbursed,
        )
        sort_col = getattr(Bill, sort_by if sort_by in SORTABLE_FIELDS else "tx_time")
        direction = sort_col.asc() if str(order).lower() == "asc" else sort_col.desc()
        return _paged(conds, page, page_size, [direction, Bill.id.desc()])

    @staticmethod
    def export_rows(
        user_id: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
        account: Optional[str] = None,
        tx_type: Optional[str] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        reimbursed: Optional[bool] = None,
    ) -> list[dict]:
        """导出用全量查询（不含回收站流水），按交易时间升序，条数上限 EXPORT_LIMIT"""
        conds = build_criteria(
            start,
            end,
            account,
            tx_type,
            category,
            user_id=user_id,
            tag=tag,
            reimbursed=reimbursed,
        )
        with get_db() as session:
            stmt = (
                select(Bill)
                .where(*conds)
                .order_by(Bill.tx_time.asc(), Bill.id.asc())
                .limit(EXPORT_LIMIT)
            )
            return [b.as_dict() for b in session.scalars(stmt)]

    @staticmethod
    def list_deleted(
        user_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[int, list[dict]]:
        """回收站分页：仅当前账号的已软删除流水，按删除前交易时间倒序"""
        conds = [Bill.user_id == user_id, Bill.deleted.is_(True)]
        return _paged(conds, page, page_size, [Bill.tx_time.desc(), Bill.id.desc()])

    @staticmethod
    def count_deleted(user_id: str) -> int:
        """回收站流水条数（当前账号）"""
        with get_db() as session:
            return session.scalar(
                select(func.count())
                .select_from(Bill)
                .where(Bill.user_id == user_id, Bill.deleted.is_(True))
            )

    @staticmethod
    def get_by_id(
        bill_id: int, user_id: str, include_deleted: bool = False
    ) -> Optional[dict]:
        """按主键查单条（仅限当前账号），不存在返回 None；回收站查看时含已删除"""
        with get_db() as session:
            conds = [Bill.id == bill_id, Bill.user_id == user_id]
            if not include_deleted:
                conds.append(Bill.deleted.is_(False))
            bill = session.scalar(select(Bill).where(*conds))
            return bill.as_dict() if bill is not None else None

    @staticmethod
    def tx_id_exists(tx_id: str, exclude_id: Optional[int] = None) -> bool:
        """交易号是否已存在（全局唯一约束，跨账号也拦截）；exclude_id 用于编辑时排除自身"""
        with get_db() as session:
            conds = [Bill.tx_id == tx_id]
            if exclude_id is not None:
                conds.append(Bill.id != exclude_id)
            return session.scalar(select(Bill.id).where(*conds).limit(1)) is not None

    @staticmethod
    def existing_tx_ids(tx_ids: list[str]) -> set[str]:
        """批量查询已存在的交易号（导入差异报告用：入库前精确区分新增/重复）

        IN 列表按 900 一段分片：SQLite 绑定变量上限 32766，PG 65535，
        大账单整包塞进单条 IN 在超限时直接抛 OperationalError、整批导入失败。
        """
        ids = [t for t in tx_ids if t]
        found: set[str] = set()
        if not ids:
            return found
        with get_db() as session:
            for chunk in in_chunks(ids):
                stmt = select(Bill.tx_id).where(Bill.tx_id.in_(chunk))
                found.update(t for t in session.scalars(stmt) if t)
        return found

    @staticmethod
    def create(data: dict, user_id: str) -> int:
        """新增单条（归属指定账号），返回自增 id

        并发下同名交易号可能越过预检查，由唯一约束兜底（转 ConflictError）。
        """
        with get_db() as session:
            with translate_unique_violation(
                "交易单号已存在", code=ErrorCode.BILL_TX_ID_DUP
            ):
                bill = Bill(**_normalize(data, user_id))
                session.add(bill)
                session.flush()
                return bill.id

    @staticmethod
    def update(bill_id: int, fields: dict, user_id: str) -> bool:
        """按白名单字段更新（仅当前账号的流水）；fields 由服务层校验后传入

        唯一约束兜底同 create（编辑交易号与并发写入冲突时转 ConflictError）。
        """
        if not fields:
            return False
        with get_db() as session:
            with translate_unique_violation(
                "交易单号已存在", code=ErrorCode.BILL_TX_ID_DUP
            ):
                rowcount = session.execute(
                    update(Bill)
                    .where(Bill.id == bill_id, Bill.user_id == user_id)
                    .values(**fields)
                ).rowcount
        return rowcount > 0

    @staticmethod
    def batch_update(ids: list[int], fields: dict, user_id: str) -> int:
        """按 id 列表批量更新（仅当前账号、单条 UPDATE），返回受影响条数"""
        if not ids or not fields:
            return 0
        with get_db() as session:
            return session.execute(
                update(Bill)
                .where(Bill.id.in_(ids), Bill.user_id == user_id)
                .values(**fields)
            ).rowcount

    @staticmethod
    def set_deleted_flag(ids: list[int], user_id: str, deleted: bool) -> int:
        """软删除 / 还原（仅当前账号），返回受影响条数"""
        return BillDAO.batch_update(ids, {"deleted": deleted}, user_id)

    @staticmethod
    def purge(ids: list[int], user_id: str) -> int:
        """彻底删除（硬删除，仅当前账号），返回删除条数"""
        if not ids:
            return 0
        with get_db() as session:
            return session.execute(
                delete(Bill).where(Bill.id.in_(ids), Bill.user_id == user_id)
            ).rowcount

    @staticmethod
    def purge_all_deleted(user_id: str) -> int:
        """清空回收站（硬删除当前账号全部已软删除流水），返回删除条数"""
        with get_db() as session:
            return session.execute(
                delete(Bill).where(Bill.user_id == user_id, Bill.deleted.is_(True))
            ).rowcount

    @staticmethod
    def count_by_category(category: str, user_id: Optional[str] = None) -> int:
        """某分类下的流水条数（user_id 为 None 时统计全部账号）

        与流水列表口径一致：排除回收站（deleted），否则分类页计数与列表
        实际条数对不上。
        """
        conds = [Bill.category == category, Bill.deleted.is_(False)]
        if user_id is not None:
            conds.append(Bill.user_id == user_id)
        with get_db() as session:
            return session.scalar(select(func.count()).select_from(Bill).where(*conds))

    @staticmethod
    def count_unassigned() -> int:
        """历史遗留的无归属流水数（升级前入库，user_id 为空串）"""
        with get_db() as session:
            return session.scalar(
                select(func.count()).select_from(Bill).where(Bill.user_id == "")
            )

    @staticmethod
    def list_for_classify(
        user_id: str,
        only_unmatched: bool = True,
        limit: int = 1000,
        after_id: Optional[int] = None,
    ) -> list[dict]:
        """取智能分类目标流水（仅当前账号），按 id 升序；only_unmatched 时仅"其他"分类

        after_id：scope=all 游标续跑用，只取该 id 之后的流水（见 ai_service）。
        """
        conds = [Bill.user_id == user_id, Bill.deleted.is_(False)]
        if only_unmatched:
            conds.append(Bill.category == DEFAULT_CATEGORY)
        if after_id:
            conds.append(Bill.id > after_id)
        with get_db() as session:
            stmt = select(Bill).where(*conds).order_by(Bill.id).limit(limit)
            return [b.as_dict() for b in session.scalars(stmt)]

    @staticmethod
    def update_categories(mapping: dict[int, str], user_id: str) -> int:
        """按 id 批量更新流水分类（仅当前账号，单事务），返回实际更新条数"""
        if not mapping:
            return 0
        changed = 0
        with get_db() as session:
            for bill_id, category in mapping.items():
                changed += session.execute(
                    update(Bill)
                    .where(Bill.id == bill_id, Bill.user_id == user_id)
                    .values(category=category)
                ).rowcount
        return changed

    @staticmethod
    def claim_unassigned(user_id: str) -> int:
        """把无归属的历史流水认领到当前账号，返回认领条数"""
        if not user_id:
            return 0
        with get_db() as session:
            return session.execute(
                update(Bill).where(Bill.user_id == "").values(user_id=user_id)
            ).rowcount
