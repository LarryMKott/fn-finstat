"""账单流水业务逻辑（数据按当前飞牛账号隔离）

按领域划分的服务类（OOP 风格）：
- BillService 承载全部业务规则（校验、归一化、唯一约束兜底）
- 依赖通过构造函数注入（DAO 单例），便于测试替换
- 保持模块级同名函数作为对 API 路由层的薄封装（保证既有调用方零改动）
- BillFilters 数据类保持模块级（被 api 层与 import_service 共用）

异常约定：业务失败抛 core.errors 的 BizError 族（由全局异常处理器统一转 HTTP
响应），本层不再抛 HTTPException，也不感知 Web 框架。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.config import DEFAULT_CATEGORY
from app.core.constants import ACCOUNTS, TX_TYPES
from app.core.errors import (
    ConflictError,
    EnvironmentError_,
    NotFoundError,
    ValidationError,
)
from app.core.errors import ErrorCode
from app.db.dao.bill_dao import BATCH_LIMIT, BillDAO, SORTABLE_FIELDS
from app.db.dao.category_dao import CategoryDAO
from app.db.models import TAGS_MAX_LENGTH
from app.schemas.bill import BillCreate, BillUpdate
from app.services import audit_service, learned_rule_service, ledger_service
from app.services.export_service import build_csv, build_xlsx
from app.utils.amount import normalize_amount

VALID_ACCOUNTS = frozenset(ACCOUNTS)

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
    "ledger_id",
}


@dataclass(frozen=True)
class BillFilters:
    """流水查询/导出共用的筛选条件（api 层以依赖注入构建，避免位置参数错位）

    categories / merchants 为多值筛选（T-6.2「存为筛选」口径复现用）：
    分类 IN 精确匹配、商户 OR 子串匹配，语义与自然语言查询一致。
    ledger_id 为账本维度（T-7.1）：None = 不按账本过滤（旧调用行为不变）。
    """

    start: Optional[str] = None
    end: Optional[str] = None
    account: Optional[str] = None
    tx_type: Optional[str] = None
    category: Optional[str] = None
    tag: Optional[str] = None
    reimbursed: Optional[bool] = None
    categories: Optional[tuple] = None
    merchants: Optional[tuple] = None
    ledger_id: Optional[int] = None

    def as_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "account": self.account,
            "tx_type": self.tx_type,
            "category": self.category,
            "tag": self.tag,
            "reimbursed": self.reimbursed,
            "categories": list(self.categories) if self.categories else None,
            "merchants": list(self.merchants) if self.merchants else None,
            "ledger_id": self.ledger_id,
        }


class BillService:
    """流水领域服务

    职责：
    - 基础校验：收支类型、账户类型、金额正数
    - 归一化：分类、标签、金额
    - 唯一约束兜底：DAO 抛 IntegrityError → ConflictError / ValidationError
    - 单条 CRUD：list / get / create / update / delete
    - 回收站：list / restore / purge / empty
    - 批量：restore / purge / batch_action（按 action 分发）
    - 导出：xlsx / csv（按筛选条件）

    协作：
    - BillDAO：所有持久化操作 + 排序白名单
    - CategoryDAO：ensure_category 自动建分类
    - export_service：xlsx / csv 字节构造
    """

    def __init__(
        self,
        bill_dao: type[BillDAO] = BillDAO,
        category_dao: type[CategoryDAO] = CategoryDAO,
    ) -> None:
        # 接受类而非实例：保留既有静态方法调用约定（DAO 是纯静态类，零状态）
        # 测试时可注入假 DAO
        self._bill_dao = bill_dao
        self._category_dao = category_dao

    # ---- 基础规则（私有，被 create/update 复用） ----

    def _validate(self, tx_type: str, account: str, amount: float) -> None:
        """新增/编辑共用的基础校验：收支类型、账户类型合法且金额为正"""
        if tx_type not in TX_TYPES:
            raise ValidationError("无效的收支类型", code=ErrorCode.BILL_INVALID)
        if account not in VALID_ACCOUNTS:
            raise ValidationError("无效的账户类型", code=ErrorCode.BILL_INVALID)
        if amount <= 0:
            raise ValidationError("金额必须大于 0", code=ErrorCode.BILL_INVALID)

    def _ensure_category(self, name: str) -> None:
        """分类不存在时自动创建，避免手工录入被拦截"""
        if name and not self._category_dao.get_by_name(name):
            self._category_dao.create(name)

    def _normalize_category(self, name: Optional[str]) -> str:
        """空分类归一化为默认分类，避免出现不在 categories 表中的孤儿分类"""
        return (name or "").strip() or DEFAULT_CATEGORY

    @staticmethod
    def normalize_tags(tags: Optional[str]) -> str:
        """标签归一化：按中英文逗号/分号/空白拆分 → 去空去重 → 逗号拼接

        写库前统一格式，配合 filters.tag 的两侧补逗号 LIKE 实现精确匹配。
        """
        if not tags:
            return ""
        seen: list[str] = []
        for part in (
            tags.replace("，", ",").replace(";", ",").replace("；", ",").split(",")
        ):
            part = part.strip()
            if part and part not in seen:
                seen.append(part)
        result: list[str] = []
        length = 0
        for tag in seen:  # 超长时整体截断（列宽 TAGS_MAX_LENGTH），丢弃放不下的标签
            if length + len(tag) + len(result) > TAGS_MAX_LENGTH:
                break
            result.append(tag)
            length += len(tag)
        return ",".join(result)

    def _check_batch_ids(self, ids: list[int]) -> None:
        if len(ids) > BATCH_LIMIT:
            raise ValidationError(f"单次批量操作最多 {BATCH_LIMIT} 条")

    # ---- 单条 CRUD ----

    def list_page(
        self,
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
            raise ValidationError("无效的排序字段", code=ErrorCode.BILL_INVALID)
        if order not in ("asc", "desc"):
            raise ValidationError("无效的排序方向", code=ErrorCode.BILL_INVALID)
        return self._bill_dao.list_bills(
            user_id,
            **filters,
            include_deleted=include_deleted,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            order=order,
        )

    def get(self, bill_id: int, user_id: str) -> dict:
        """查单条账单，不存在抛 NotFoundError"""
        bill = self._bill_dao.get_by_id(bill_id, user_id)
        if bill is None:
            raise NotFoundError("账单不存在")
        return bill

    def create(
        self, data: BillCreate, user_id: str, ledger_id: Optional[int] = None
    ) -> dict:
        """新增账单：基础校验 → 交易号查重 → 分类归一化并确保存在 → 入库

        ledger_id 为 None 时落到默认账本（T-7.1 向后兼容：升级前全部流水都在
        默认账本上，旧调用不传该参数因此行为不变）。
        """
        self._validate(data.tx_type, data.account, data.amount)
        # 账本维度（T-7.1）：写路径显式校验账本存在，避免把流水写进不存在的账本
        ledger_id = ledger_service.resolve_write(ledger_id)
        if data.tx_id and self._bill_dao.tx_id_exists(data.tx_id):
            raise ConflictError("交易单号已存在", code=ErrorCode.BILL_TX_ID_DUP)
        payload = data.model_dump()
        payload["category"] = self._normalize_category(payload["category"])
        self._ensure_category(payload["category"])
        payload["amount"] = normalize_amount(payload["amount"])
        payload["tags"] = self.normalize_tags(payload["tags"])
        # DAO 层以唯一约束兜底并发重复（转 ConflictError），此处无需再捕获
        bill_id = self._bill_dao.create(payload, user_id, ledger_id)
        bill = self._bill_dao.get_by_id(bill_id, user_id)
        if bill is None:
            raise EnvironmentError_("新增失败：写入后无法取回记录")
        audit_service.record(
            user_id,
            "bill.create",
            "bill",
            bill_id,
            "新增流水："
            + (bill["merchant"] or "（无商户）")
            + f" {bill['amount']} 元（{bill['category']}，{bill['tx_time'][:10]}）",
        )
        return bill

    def update(self, bill_id: int, data: BillUpdate, user_id: str) -> dict:
        """部分更新：仅处理请求中显式传入且非空的字段，校验规则与新增保持一致"""
        existing = self._bill_dao.get_by_id(bill_id, user_id)
        if existing is None:
            raise NotFoundError("账单不存在")

        raw = data.model_dump(exclude_unset=True)
        fields = {k: v for k, v in raw.items() if k in _UPDATE_FIELDS and v is not None}
        # 空交易号归一化为 None（UNIQUE 允许多个 NULL，空串全局只允许一条）
        if fields.get("tx_id") == "":
            fields["tx_id"] = None

        if "tx_type" in fields and fields["tx_type"] not in TX_TYPES:
            raise ValidationError("无效的收支类型", code=ErrorCode.BILL_INVALID)
        if "account" in fields and fields["account"] not in VALID_ACCOUNTS:
            raise ValidationError("无效的账户类型", code=ErrorCode.BILL_INVALID)
        if "amount" in fields and fields["amount"] <= 0:
            raise ValidationError("金额必须大于 0", code=ErrorCode.BILL_INVALID)
        if "amount" in fields:
            fields["amount"] = normalize_amount(fields["amount"])
        if "category" in fields:
            # 空分类归一化为默认分类，与新增逻辑一致
            fields["category"] = self._normalize_category(fields["category"])
            self._ensure_category(fields["category"])
        if "tags" in fields:
            fields["tags"] = self.normalize_tags(fields["tags"])
        if "ledger_id" in fields:
            # T-7.1 评审遗留：支持把流水移动到其他账本。显式 null 视为不迁移
            # （exclude_unset 已区分「未传」），传值则走写路径校验账本存在
            target = fields.pop("ledger_id")
            if target is not None:
                fields["ledger_id"] = ledger_service.resolve_write(target)
        if (
            "tx_id" in fields
            and fields.get("tx_id")
            and self._bill_dao.tx_id_exists(fields["tx_id"], exclude_id=bill_id)
        ):
            raise ConflictError("交易单号已存在", code=ErrorCode.BILL_TX_ID_DUP)

        # 唯一约束兜底并发重复（DAO 层转 ConflictError）
        self._bill_dao.update(bill_id, fields, user_id)
        updated = self._bill_dao.get_by_id(bill_id, user_id)
        if updated is None:
            raise EnvironmentError_("更新失败：写入后无法取回记录")
        diff = audit_service.diff_summary(
            existing,
            updated,
            {
                "tx_time": "交易时间",
                "merchant": "商户",
                "amount": "金额",
                "category": "分类",
                "tags": "标签",
                "reimbursed": "报销",
                "account": "账户",
                "tx_id": "交易号",
                "remark": "备注",
                "ledger_id": "账本",
            },
        )
        audit_service.record(
            user_id,
            "bill.update",
            "bill",
            bill_id,
            "编辑流水 #"
            + str(bill_id)
            + " "
            + (existing["merchant"] or "（无商户）")
            + "："
            + (diff or "无字段变化"),
        )
        self._learn_correction(existing, fields, updated)
        return updated

    @staticmethod
    def _learn_correction(old: dict, fields: dict, updated: dict) -> None:
        """手动纠正分类时沉淀学习规则（T-6.3，best-effort 不影响主流程）"""
        if "category" not in fields or fields["category"] == old["category"]:
            return
        learned_rule_service.record_correction(updated["merchant"], fields["category"])

    def delete(self, bill_id: int, user_id: str) -> None:
        """删除账单：移入回收站（软删除）；不存在或已在回收站抛 NotFoundError"""
        existing = self._bill_dao.get_by_id(bill_id, user_id)
        if existing is None:  # get_by_id 默认排除已删除
            raise NotFoundError("账单不存在")
        self._bill_dao.set_deleted_flag([bill_id], user_id, True)
        # 回收站流水不留在报销单内（T-7.4）：摘除关联并复位报销标记
        self._bill_dao.clear_claims(user_id, [bill_id])
        audit_service.record(
            user_id,
            "bill.delete",
            "bill",
            bill_id,
            "移入回收站："
            + (existing["merchant"] or "（无商户）")
            + f" {existing['amount']} 元",
        )

    # ---- 回收站 ----

    def list_deleted(
        self, user_id: str, page: int, page_size: int
    ) -> tuple[int, list[dict]]:
        """回收站分页"""
        return self._bill_dao.list_deleted(user_id, page=page, page_size=page_size)

    def restore(self, ids: list[int], user_id: str) -> int:
        """从回收站还原，返回还原条数"""
        self._check_batch_ids(ids)
        affected = self._bill_dao.set_deleted_flag(ids, user_id, False)
        if affected:
            audit_service.record(
                user_id,
                "bill.restore",
                "bill",
                None,
                "从回收站还原 " + str(affected) + " 条流水",
            )
        return affected

    def purge(self, ids: list[int], user_id: str) -> int:
        """彻底删除（回收站场景），返回删除条数"""
        self._check_batch_ids(ids)
        affected = self._bill_dao.purge(ids, user_id)
        if affected:
            audit_service.record(
                user_id,
                "bill.purge",
                "bill",
                None,
                "彻底删除 " + str(affected) + " 条流水",
            )
        return affected

    def empty_recycle(self, user_id: str) -> int:
        """清空当前账号回收站，返回删除条数"""
        affected = self._bill_dao.purge_all_deleted(user_id)
        if affected:
            audit_service.record(
                user_id,
                "bill.recycle_empty",
                "bill",
                None,
                "清空回收站 " + str(affected) + " 条流水",
            )
        return affected

    # ---- 批量 ----

    def batch_action(self, payload, user_id: str) -> int:
        """流水批量操作（多选后统一处理），返回受影响条数

        payload 为 BatchBillRequest；按 action 字段分发。
        """
        ids = payload.ids
        self._check_batch_ids(ids)
        action = payload.action
        if action == "delete":
            affected = self._bill_dao.set_deleted_flag(ids, user_id, True)
            # 回收站流水不留在报销单内（T-7.4）
            self._bill_dao.clear_claims(user_id, ids)
            if affected:
                audit_service.record(
                    user_id,
                    "bill.batch",
                    "bill",
                    None,
                    "批量移入回收站 " + str(affected) + " 条流水",
                )
            return affected
        if action == "restore":
            affected = self._bill_dao.set_deleted_flag(ids, user_id, False)
            if affected:
                audit_service.record(
                    user_id,
                    "bill.batch",
                    "bill",
                    None,
                    "批量还原 " + str(affected) + " 条流水",
                )
            return affected
        if action == "purge":
            affected = self._bill_dao.purge(ids, user_id)
            if affected:
                audit_service.record(
                    user_id,
                    "bill.batch",
                    "bill",
                    None,
                    "批量彻底删除 " + str(affected) + " 条流水",
                )
            return affected
        if action == "set_category":
            category = (payload.category or "").strip()
            if not category:
                raise ValidationError("请选择目标分类")
            self._ensure_category(category)
            # 学习钩子（T-6.3）：批量纠正按「去重后的商户 pattern」各记一次证据
            # ——一次批量动作对一个商户算一次纠正，不按流水条数膨胀 hits
            before = {b["id"]: b for b in self._bill_dao.list_by_ids(ids, user_id)}
            affected = self._bill_dao.batch_update(ids, {"category": category}, user_id)
            seen_patterns: set[str] = set()
            for bill in before.values():
                if bill["category"] == category:
                    continue
                pattern = learned_rule_service.extract_pattern(bill["merchant"])
                if pattern and pattern not in seen_patterns:
                    seen_patterns.add(pattern)
                    learned_rule_service.record_correction(bill["merchant"], category)
            audit_service.record(
                user_id,
                "bill.batch",
                "bill",
                None,
                "批量改分类 " + str(affected) + " 条流水 → 「" + category + "」",
            )
            return affected
        if action == "set_tags":
            tags = self.normalize_tags(payload.tags)
            affected = self._bill_dao.batch_update(ids, {"tags": tags}, user_id)
            if affected:
                audit_service.record(
                    user_id,
                    "bill.batch",
                    "bill",
                    None,
                    "批量打标签 " + str(affected) + " 条流水 → 「" + tags + "」",
                )
            return affected
        if action == "set_reimbursed":
            if payload.reimbursed is None:
                raise ValidationError("请指定报销标记")
            affected = self._bill_dao.batch_update(
                ids, {"reimbursed": payload.reimbursed}, user_id
            )
            audit_service.record(
                user_id,
                "bill.batch",
                "bill",
                None,
                "批量报销标记 "
                + str(affected)
                + " 条流水 = "
                + ("已报销" if payload.reimbursed else "未报销"),
            )
            return affected
        raise ValidationError("无效的批量操作")

    # ---- 导出 ----

    def export(
        self,
        user_id: str,
        filters: dict,
        fmt: str = "xlsx",
    ) -> tuple[str, bytes, str]:
        """按筛选条件导出流水（不含回收站），返回 (文件名, 内容字节, MIME)

        返回条数受 EXPORT_LIMIT 限制；fmt 仅支持 xlsx / csv。
        """
        fmt = (fmt or "xlsx").lower()
        if fmt not in ("xlsx", "csv"):
            raise ValidationError("仅支持 xlsx / csv 导出格式")
        bills = self._bill_dao.export_rows(user_id, **filters)
        if fmt == "csv":
            content = build_csv(bills)
            media_type = "text/csv; charset=utf-8"
        else:
            content = build_xlsx(bills)
            media_type = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        date_prefix = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"流水导出-{date_prefix}.{fmt}"
        return filename, content, media_type


# ---- 模块级薄封装：保留既有 API 路由调用方式（保证零改动） ----
# 单例：服务无内部状态，模块级单例等价于每次 new，但避免重复构造
_service = BillService()


def list_bills(
    user_id: str,
    filters: dict,
    page: int,
    page_size: int,
    sort_by: str = "tx_time",
    order: str = "desc",
    include_deleted: bool = False,
) -> tuple[int, list[dict]]:
    return _service.list_page(
        user_id, filters, page, page_size, sort_by, order, include_deleted
    )


def get_bill(bill_id: int, user_id: str) -> dict:
    return _service.get(bill_id, user_id)


def create_bill(
    data: BillCreate, user_id: str, ledger_id: Optional[int] = None
) -> dict:
    return _service.create(data, user_id, ledger_id)


def update_bill(bill_id: int, data: BillUpdate, user_id: str) -> dict:
    return _service.update(bill_id, data, user_id)


def delete_bill(bill_id: int, user_id: str) -> None:
    return _service.delete(bill_id, user_id)


def list_recycle(user_id: str, page: int, page_size: int) -> tuple[int, list[dict]]:
    return _service.list_deleted(user_id, page, page_size)


def restore_bills(ids: list[int], user_id: str) -> int:
    return _service.restore(ids, user_id)


def purge_bills(ids: list[int], user_id: str) -> int:
    return _service.purge(ids, user_id)


def empty_recycle(user_id: str) -> int:
    return _service.empty_recycle(user_id)


def batch_action(payload, user_id: str) -> int:
    return _service.batch_action(payload, user_id)


def export_bills(
    user_id: str,
    filters: dict,
    fmt: str = "xlsx",
) -> tuple[str, bytes, str]:
    return _service.export(user_id, filters, fmt)


def normalize_tags(tags: Optional[str]) -> str:
    """向后兼容：模块级同名入口，供历史导入路径使用

    历史调用形如 `from app.services.bill_service import normalize_tags`
    （见 import_service 等存量导入），改成类方法后保留此薄封装以免破坏调用方。
    新代码请直接使用 `BillService.normalize_tags(...)` 或实例方法 `.normalize_tags(...)`，
    两者实现完全等价（类方法是 @staticmethod，无实例状态依赖）。
    """
    return BillService.normalize_tags(tags)
