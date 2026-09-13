"""账单流水业务逻辑（数据按当前飞牛账号隔离）"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException

from app.config import DEFAULT_CATEGORY
from app.db.base import UniqueViolationError
from app.db.dao.bill_dao import BATCH_LIMIT, BillDAO, SORTABLE_FIELDS
from app.db.dao.category_dao import CategoryDAO
from app.schemas.bill import BillCreate, BillUpdate
from app.services.export_service import build_csv, build_xlsx
from app.utils.amount import normalize_amount

VALID_TYPES = {"expense", "income", "transfer"}
VALID_ACCOUNTS = {"wechat", "alipay", "jd", "unionpay"}

# 允许被更新的字段白名单（防止 SQL 注入与越权字段）
_UPDATE_FIELDS = {
    "tx_time",
    "account",
    "tx_type",
    "merchant",
    "amount",
    "category",
    "tx_id",
    "remark",
    "tags",
    "reimbursed",
}


def _validate(tx_type: str, account: str, amount: float) -> None:
    """新增/编辑共用的基础校验：收支类型、账户类型合法且金额为正"""
    if tx_type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail="无效的收支类型")
    if account not in VALID_ACCOUNTS:
        raise HTTPException(status_code=400, detail="无效的账户类型")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="金额必须大于 0")


def _ensure_category(name: str) -> None:
    """分类不存在时自动创建，避免手工录入被拦截"""
    if name and not CategoryDAO.get_by_name(name):
        CategoryDAO.create(name)


def normalize_tags(tags: Optional[str]) -> str:
    """标签归一化：按中英文逗号/分号/空白拆分 → 去空去重 → 逗号拼接

    写库前统一格式，配合 filters.tag 的两侧补逗号 LIKE 实现精确匹配。
    """
    if not tags:
        return ""
    seen: list[str] = []
    for part in tags.replace("，", ",").replace(";", ",").replace("；", ",").split(","):
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    result: list[str] = []
    length = 0
    for tag in seen:  # 超长时整体截断（列宽 255），丢弃放不下的标签
        if length + len(tag) + len(result) > 255:
            break
        result.append(tag)
        length += len(tag)
    return ",".join(result)


def list_bills(
    user_id: str,
    filters: dict,
    page: int,
    page_size: int,
    sort_by: str = "tx_time",
    order: str = "desc",
    include_deleted: bool = False,
) -> tuple[int, list[dict]]:
    """分页查询账单（排序字段/方向先经白名单校验，再交由 DAO 排序）"""
    # 排序白名单以 DAO 层 SORTABLE_FIELDS 为单一来源
    if sort_by not in SORTABLE_FIELDS:
        raise HTTPException(status_code=400, detail="无效的排序字段")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="无效的排序方向")
    return BillDAO.list_bills(
        user_id,
        **filters,
        include_deleted=include_deleted,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        order=order,
    )


def get_bill(bill_id: int, user_id: str) -> dict:
    """查单条账单，不存在抛 404"""
    bill = BillDAO.get_by_id(bill_id, user_id)
    if bill is None:
        raise HTTPException(status_code=404, detail="账单不存在")
    return bill


def create_bill(data: BillCreate, user_id: str) -> dict:
    """新增账单：基础校验 → 交易号查重 → 分类归一化并确保存在 → 入库"""
    _validate(data.tx_type, data.account, data.amount)
    if data.tx_id and BillDAO.tx_id_exists(data.tx_id):
        raise HTTPException(status_code=400, detail="交易单号已存在")
    payload = data.model_dump()
    # 空分类归一化为默认分类，避免出现不在 categories 表中的孤儿分类
    payload["category"] = (payload["category"] or "").strip() or DEFAULT_CATEGORY
    _ensure_category(payload["category"])
    payload["amount"] = normalize_amount(payload["amount"])
    payload["tags"] = normalize_tags(payload["tags"])
    try:
        bill_id = BillDAO.create(payload, user_id)
    except UniqueViolationError:  # 并发下同名交易号越过预检查，由唯一约束兜底
        raise HTTPException(status_code=400, detail="交易单号已存在")
    bill = BillDAO.get_by_id(bill_id, user_id)
    if bill is None:
        raise HTTPException(status_code=500, detail="新增失败")
    return bill


def update_bill(bill_id: int, data: BillUpdate, user_id: str) -> dict:
    """部分更新：仅处理请求中显式传入且非空的字段，校验规则与新增保持一致"""
    if BillDAO.get_by_id(bill_id, user_id) is None:
        raise HTTPException(status_code=404, detail="账单不存在")

    raw = data.model_dump(exclude_unset=True)
    fields = {k: v for k, v in raw.items() if k in _UPDATE_FIELDS and v is not None}
    # 空交易号归一化为 None（UNIQUE 允许多个 NULL，空串全局只允许一条）
    if fields.get("tx_id") == "":
        fields["tx_id"] = None

    if "tx_type" in fields and fields["tx_type"] not in VALID_TYPES:
        raise HTTPException(status_code=400, detail="无效的收支类型")
    if "account" in fields and fields["account"] not in VALID_ACCOUNTS:
        raise HTTPException(status_code=400, detail="无效的账户类型")
    if "amount" in fields and fields["amount"] <= 0:
        raise HTTPException(status_code=400, detail="金额必须大于 0")
    if "amount" in fields:
        fields["amount"] = normalize_amount(fields["amount"])
    if "category" in fields:
        # 空分类归一化为默认分类，与新增逻辑一致
        fields["category"] = (fields["category"] or "").strip() or DEFAULT_CATEGORY
        _ensure_category(fields["category"])
    if "tags" in fields:
        fields["tags"] = normalize_tags(fields["tags"])
    if (
        "tx_id" in fields
        and fields.get("tx_id")
        and BillDAO.tx_id_exists(fields["tx_id"], exclude_id=bill_id)
    ):
        raise HTTPException(status_code=400, detail="交易单号已存在")

    try:
        BillDAO.update(bill_id, fields, user_id)
    except UniqueViolationError:  # 并发下同名交易号越过预检查，由唯一约束兜底
        raise HTTPException(status_code=400, detail="交易单号已存在")
    updated = BillDAO.get_by_id(bill_id, user_id)
    if updated is None:
        raise HTTPException(status_code=500, detail="更新失败")
    return updated


def delete_bill(bill_id: int, user_id: str) -> None:
    """删除账单：移入回收站（软删除）；不存在或已在回收站抛 404"""
    if BillDAO.get_by_id(bill_id, user_id) is None:  # get_by_id 默认排除已删除
        raise HTTPException(status_code=404, detail="账单不存在")
    BillDAO.set_deleted_flag([bill_id], user_id, True)


def list_recycle(user_id: str, page: int, page_size: int) -> tuple[int, list[dict]]:
    """回收站分页"""
    return BillDAO.list_deleted(user_id, page=page, page_size=page_size)


def recycle_count(user_id: str) -> int:
    """回收站条数（用于角标提示）"""
    return BillDAO.count_deleted(user_id)


def _check_batch_ids(ids: list[int]) -> None:
    if len(ids) > BATCH_LIMIT:
        raise HTTPException(
            status_code=400, detail=f"单次批量操作最多 {BATCH_LIMIT} 条"
        )


def restore_bills(ids: list[int], user_id: str) -> int:
    """从回收站还原，返回还原条数"""
    _check_batch_ids(ids)
    return BillDAO.set_deleted_flag(ids, user_id, False)


def purge_bills(ids: list[int], user_id: str) -> int:
    """彻底删除（回收站场景），返回删除条数"""
    _check_batch_ids(ids)
    return BillDAO.purge(ids, user_id)


def empty_recycle(user_id: str) -> int:
    """清空当前账号回收站，返回删除条数"""
    return BillDAO.purge_all_deleted(user_id)


def batch_action(payload, user_id: str) -> int:
    """流水批量操作（多选后统一处理），返回受影响条数

    payload 为 BatchBillRequest；拆成独立函数便于服务层复用与测试。
    """
    ids = payload.ids
    _check_batch_ids(ids)
    action = payload.action
    if action == "delete":
        return BillDAO.set_deleted_flag(ids, user_id, True)
    if action == "restore":
        return BillDAO.set_deleted_flag(ids, user_id, False)
    if action == "purge":
        return BillDAO.purge(ids, user_id)
    if action == "set_category":
        category = (payload.category or "").strip()
        if not category:
            raise HTTPException(status_code=400, detail="请选择目标分类")
        _ensure_category(category)
        return BillDAO.batch_update(ids, {"category": category}, user_id)
    if action == "set_tags":
        return BillDAO.batch_update(
            ids, {"tags": normalize_tags(payload.tags)}, user_id
        )
    if action == "set_reimbursed":
        if payload.reimbursed is None:
            raise HTTPException(status_code=400, detail="请指定报销标记")
        return BillDAO.batch_update(ids, {"reimbursed": payload.reimbursed}, user_id)
    raise HTTPException(status_code=400, detail="无效的批量操作")


def export_bills(
    user_id: str,
    filters: dict,
    fmt: str = "xlsx",
) -> tuple[str, bytes, str]:
    """按筛选条件导出流水（不含回收站），返回 (文件名, 内容字节, MIME)

    返回条数受 EXPORT_LIMIT 限制；fmt 仅支持 xlsx / csv。
    """
    fmt = (fmt or "xlsx").lower()
    if fmt not in ("xlsx", "csv"):
        raise HTTPException(status_code=400, detail="仅支持 xlsx / csv 导出格式")
    bills = BillDAO.export_rows(user_id, **filters)
    if fmt == "csv":
        content = build_csv(bills)
        media_type = "text/csv; charset=utf-8"
    else:
        content = build_xlsx(bills)
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    date_prefix = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"流水导出-{date_prefix}.{fmt}"
    return filename, content, media_type


def month_range(month: str) -> tuple[str, str]:
    """月份（YYYY-MM）→ 当月起止日期（含端点），供筛选/统计复用"""
    try:
        year, mon = int(month[:4]), int(month[5:7])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"无效的月份：{month}")
    if not 1 <= mon <= 12:
        raise HTTPException(status_code=400, detail=f"无效的月份：{month}")
    start = f"{year:04d}-{mon:02d}-01"
    next_y, next_m = (year + 1, 1) if mon == 12 else (year, mon + 1)
    end = (datetime(next_y, next_m, 1) - timedelta(days=1)).strftime("%Y-%m-%d")
    return start, end
