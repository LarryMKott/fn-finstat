"""账本业务逻辑（T-7.1 账本维度）

账本是流水 / 预算 / 资产快照的归属维度，本服务提供：
- 列表（含各账本流水条数）与默认账本解析
- 新建 / 改名 / 删除（写操作由路由层 require_admin 收口）
- 写路径统一的账本解析：None = 默认账本，其余校验存在后原样返回。
  读路径不经过解析——ledger_id 与分类/商户筛选同为普通过滤维度，
  不存在的账本 id 只是过滤出空结果（与分类筛选行为一致），不做 404。

删除语义：默认账本不可删除；删除其他账本前先把该账本下的流水 / 预算 / 资产
快照并回默认账本，避免「删账本 = 删数据」这种不可逆操作。
"""

from typing import Optional

from app.core.errors import NotFoundError, ValidationError
from app.db.dao.ledger_dao import LedgerDAO
from app.schemas.ledger import LedgerCreate, LedgerUpdate

# 账本名长度上限（与列宽 String(64) 对齐，留足中文长度）
LEDGER_NAME_MAX = 64

# 允许被更新的字段白名单（防止越权字段写入）
_UPDATABLE_FIELDS = {"name", "remark"}


def list_ledgers() -> list[dict]:
    """全部账本（默认账本在前），含各账本未删除流水条数"""
    return LedgerDAO.list_with_counts()


def default_id() -> int:
    """默认账本 id（不存在时创建）"""
    return LedgerDAO.default_id()


def resolve_write(ledger_id: Optional[int]) -> int:
    """写路径账本解析：None / 0 落到默认账本，其余校验存在后原样返回"""
    if not ledger_id:
        return LedgerDAO.default_id()
    if LedgerDAO.get(int(ledger_id)) is None:
        raise NotFoundError("账本不存在")
    return int(ledger_id)


def create_ledger(payload: LedgerCreate) -> dict:
    """新建账本（名称非空且唯一）"""
    name = (payload.name or "").strip()
    if not name:
        raise ValidationError("账本名不能为空")
    if len(name) > LEDGER_NAME_MAX:
        raise ValidationError(f"账本名不能超过 {LEDGER_NAME_MAX} 个字符")
    # owner_id 恒为空串（应用级共享）：成员账本归属要等 T-7.2 家庭空间确定
    return LedgerDAO.create(
        name=name, owner_id="", remark=(payload.remark or "").strip()
    )


def update_ledger(ledger_id: int, payload: LedgerUpdate) -> dict:
    """改名 / 改备注（仅更新非空字段）"""
    fields = {}
    name = payload.name
    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise ValidationError("账本名不能为空")
        if len(cleaned) > LEDGER_NAME_MAX:
            raise ValidationError(f"账本名不能超过 {LEDGER_NAME_MAX} 个字符")
        fields["name"] = cleaned
    if payload.remark is not None:
        fields["remark"] = payload.remark.strip()
    updated = LedgerDAO.update_fields(ledger_id, fields)
    if updated is None:
        raise NotFoundError("账本不存在")
    return updated


def delete_ledger(ledger_id: int) -> dict:
    """删除账本：默认账本拒绝删除，其余先并回默认账本再删

    返回并入默认账本的数据条数（moved_bills / moved_budgets / moved_assets，
    预算同键冲突被丢弃的行数记 dropped_budgets），供前端提示。
    """
    ledger = LedgerDAO.get(ledger_id)
    if ledger is None:
        raise NotFoundError("账本不存在")
    if ledger["is_default"]:
        raise ValidationError("默认账本不可删除")
    moved = LedgerDAO.delete(ledger_id)
    if moved is None:  # 并发下刚被置为默认账本（或已被删除）时兜底
        raise ValidationError("默认账本不可删除")
    return {
        "id": ledger_id,
        "moved_to_default": True,
        "moved_bills": moved["bills"],
        "moved_budgets": moved["budgets"],
        "moved_assets": moved["assets"],
        "dropped_budgets": moved["dropped_budgets"],
    }
