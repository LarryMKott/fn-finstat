"""全量数据备份与恢复（JSON 文件，兼容 SQLite / MySQL / PostgreSQL）

- 备份：导出全部账号的 分类 / 账本 / 流水 / 预算 / 资产快照 为一个 JSON 文件下载
- 恢复：
    replace=False（默认）合并模式——按唯一键去重导入（流水 tx_id、分类名、预算唯一键；
      无交易号的流水无法去重，重复恢复同一备份可能产生重复记录）
    replace=True 覆盖模式——先清空全部业务表再导入（不可恢复，前端需二次确认）
- 备份文件不包含数据库连接配置与 AI/日志等运行配置，仅业务数据

账本维度（T-7.1）：备份含 ledgers 节，流水/预算/快照带 ledger_id。恢复时按
**账本名**重映射 id（目标库的账本 id 与备份中的不一定相同），映射不到或旧备份
无 ledger_id 时一律落到默认账本——因此旧备份可直接恢复到新版本，无需转换。
恢复后默认账本全局唯一：合并模式本地默认优先、覆盖模式以备份为准，备份带来的
多余 is_default 标记统一降级（is_default 无唯一约束，靠此处收口）。
"""

import json
import logging
import threading
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, select, update

from app.core.constants import (
    ASSET_TYPES,
    family_scope_user,
    ASSET_TYPE_ASSET,
    FAMILY_ROLES,
    ROLE_MEMBER,
    TX_TYPES,
)
from app.core.errors import ValidationError
from app.db.base import LATEST_SCHEMA_VERSION, get_db, insert_ignore_rows
from app.db.ledgers import ensure_default_ledger
from app.db.models import (
    AssetSnapshot,
    Bill,
    Budget,
    Category,
    Family,
    FamilyMember,
    Ledger,
)

logger = logging.getLogger(__name__)

BACKUP_FORMAT_VERSION = (
    3  # v3：budgets 行新增 family_id（T-7.3 家庭预算）；v2 备份缺该列时按个人预算恢复
)

# 恢复与导入共用一把进程级互斥锁：恢复（尤其 replace 模式）期间并发导入的
# 写入会「穿越」清空点残留，最终库状态既非纯备份也非纯现况。恢复侧独占；
# 导入侧（import_service.import_local_file）以非阻塞方式尝试获取，恢复进行中
# 直接拒绝新导入并提示用户。
RESTORE_LOCK = threading.Lock()

# 备份文件键 → ORM 模型（导出与恢复共用）
_SECTIONS = {
    "categories": Category,
    "ledgers": Ledger,
    "families": Family,
    "family_members": FamilyMember,
    "bills": Bill,
    "budgets": Budget,
    "assets": AssetSnapshot,
}

# 各节数据的白名单字段（列名 → 是否可空），防止恶意 JSON 注入未知键
_FIELDS = {
    "categories": {"name"},
    "bills": {
        "user_id",
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
        "deleted",
        "ledger_id",
    },
    "ledgers": {"name", "owner_id", "is_default", "remark"},
    "families": {"name", "invite_code", "allow_detail_view", "created_by"},
    # family_id 不在白名单：恢复时按邀请码重映射到目标库新 id（见 restore_backup）
    "family_members": {"user_id", "role", "nickname", "joined_at"},
    "budgets": {
        "user_id",
        "ledger_id",
        "month",
        "category",
        "amount",
        "family_id",
    },
    "assets": {
        "user_id",
        "ledger_id",
        "snap_date",
        "name",
        "asset_type",
        "amount",
        "remark",
    },
}


def _coerce_ledger_id(value) -> Optional[int]:
    """账本 id 容错：非法值（缺字段 / 字符串 / 负数）统一转 None，由调用方落到默认账本"""
    try:
        ledger_id = int(value)
    except (TypeError, ValueError):
        return None
    return ledger_id if ledger_id > 0 else None


def _ledger_id_to_name(data: dict) -> dict[int, str]:
    """备份中「账本 id → 账本名」：恢复时按名重映射用（目标库 id 与备份不一定相同）"""
    mapping: dict[int, str] = {}
    for raw in data.get("ledgers") or []:
        if not isinstance(raw, dict):
            continue
        ledger_id = _coerce_ledger_id(raw.get("id"))
        name = str(raw.get("name") or "").strip()
        if ledger_id and name:
            mapping[ledger_id] = name
    return mapping


def _family_id_to_code(data: dict) -> dict[int, str]:
    """备份中「家庭 id → 邀请码」：恢复时按码重映射成员行用（邀请码全局唯一）"""
    mapping: dict[int, str] = {}
    for raw in data.get("families") or []:
        if not isinstance(raw, dict):
            continue
        family_id = _coerce_ledger_id(raw.get("id"))
        code = str(raw.get("invite_code") or "").strip().upper()
        if family_id and code:
            mapping[family_id] = code
    return mapping


def export_backup() -> dict:
    """导出全库业务数据为可 JSON 序列化的字典"""
    data = {
        "app": "fn-finstat",
        "format_version": BACKUP_FORMAT_VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "schema_version": LATEST_SCHEMA_VERSION,
    }
    with get_db() as session:
        # v3 起为对象数组 [{"name": ...}]；v2 及更早备份是纯字符串数组，
        # 恢复端两种形态都接受（曾因字符串被 _clean_row 判为坏行，
        # 导致 replace 模式恢复丢光全部分类）
        data["categories"] = [
            {"name": c.name} for c in session.scalars(select(Category))
        ]
        # 账本保留 id：恢复时据此把流水/预算/快照的 ledger_id 按名重映射到新 id
        data["ledgers"] = [l.as_dict() for l in session.scalars(select(Ledger))]
        # 家庭保留 id：恢复时成员行的 family_id 按邀请码重映射到新 id
        data["families"] = [f.as_dict() for f in session.scalars(select(Family))]
        data["family_members"] = [
            m.as_dict() for m in session.scalars(select(FamilyMember))
        ]
        data["bills"] = [b.as_dict() for b in session.scalars(select(Bill))]
        data["budgets"] = [b.as_dict() for b in session.scalars(select(Budget))]
        data["assets"] = [a.as_dict() for a in session.scalars(select(AssetSnapshot))]
    # 流水的 id 由目标库自增，不导出
    for bill in data["bills"]:
        bill.pop("id", None)
    # budgets/assets 不保留 id；ledgers/families 保留 id（恢复时按名/码重映射用，
    # 见 _ledger_id_to_name / _family_id_to_code；入库前由 _clean_row 剥掉）
    for section in ("budgets", "assets", "family_members"):
        for row in data[section]:
            row.pop("id", None)
    return data


def _clean_row(section: str, raw: dict) -> Optional[dict]:
    """按白名单字段清洗一行数据；类型不合法返回 None（恢复时跳过）"""
    if not isinstance(raw, dict):
        return None
    row = {k: raw.get(k) for k in _FIELDS[section]}
    if section == "categories":
        if isinstance(raw, str):  # v2 及更早备份：纯分类名数组
            name = raw.strip()
            return {"name": name[:64]} if name else None
        name = str(row.get("name") or "").strip()
        return {"name": name[:64]} if name else None
    if section == "bills":
        if not str(row.get("tx_time") or "").strip():
            return None
        if str(row.get("tx_type")) not in TX_TYPES:
            return None
        try:
            if float(row.get("amount") or 0) <= 0:
                return None
        except (TypeError, ValueError):
            return None
        if not row.get("tx_id"):
            row["tx_id"] = None  # 空交易号转 NULL，配合唯一约束
        row["amount"] = round(float(row["amount"]), 2)
        row["ledger_id"] = _coerce_ledger_id(row.get("ledger_id"))
        # 白名单取值把缺失键填成 None，会在 insert_ignore_rows 撞 NOT NULL 被
        # 静默丢弃（结果计数却照常 +1），可选字段在此按列默认值补齐
        row.setdefault("user_id", "")
        row["account"] = str(row.get("account") or "wechat")
        for key in ("merchant", "remark", "tags"):
            row[key] = str(row.get(key) or "")
        row["reimbursed"] = bool(row.get("reimbursed"))
        row["deleted"] = bool(row.get("deleted"))
        return row
    if section == "ledgers":
        name = str(row.get("name") or "").strip()
        if not name:
            return None
        return {
            "name": name[:64],
            "owner_id": str(row.get("owner_id") or "")[:32],
            "is_default": bool(row.get("is_default")),
            "remark": str(row.get("remark") or "")[:255],
        }
    if section == "families":
        code = str(row.get("invite_code") or "").strip().upper()
        name = str(row.get("name") or "").strip()
        if not code or not name:
            return None
        return {
            "name": name[:64],
            "invite_code": code[:16],
            "allow_detail_view": bool(row.get("allow_detail_view")),
            "created_by": str(row.get("created_by") or "")[:32],
        }
    if section == "family_members":
        user_id = str(row.get("user_id") or "").strip()
        if not user_id:
            return None
        role = str(row.get("role") or "member")
        try:
            joined_at = float(row.get("joined_at") or 0)
        except (TypeError, ValueError):
            joined_at = 0.0
        return {
            "user_id": user_id[:32],
            "role": role if role in FAMILY_ROLES else ROLE_MEMBER,
            "nickname": str(row.get("nickname") or "")[:64],
            "joined_at": joined_at,
        }
    if section == "budgets":
        if not str(row.get("month") or "").strip():
            return None  # month 缺失直接跳过，避免静默丢弃却计入恢复数
        row["user_id"] = str(row.get("user_id") or "")
        row["category"] = str(row.get("category") or "")
        try:
            amount = float(row.get("amount") or 0)
        except (TypeError, ValueError):
            return None
        if amount <= 0:
            return None
        row["amount"] = round(amount, 2)
        row["ledger_id"] = _coerce_ledger_id(row.get("ledger_id"))
        # 家庭预算行（T-7.3）：family_id 在恢复阶段按邀请码重映射为负数哨兵前的
        # 原始 id，无法解析时置 None 由恢复阶段按孤儿行跳过（见 budgets 恢复段）
        row["family_id"] = _coerce_ledger_id(row.get("family_id"))
        return row
    if section == "assets":
        if not str(row.get("snap_date") or "").strip():
            return None
        row["user_id"] = str(row.get("user_id") or "")
        row["name"] = str(row.get("name") or "")
        row["remark"] = str(row.get("remark") or "")
        try:
            amount = float(row.get("amount") or 0)
        except (TypeError, ValueError):
            return None
        if amount < 0:
            return None
        row["asset_type"] = (
            ASSET_TYPE_ASSET
            if row.get("asset_type") not in ASSET_TYPES
            else row["asset_type"]
        )
        row["amount"] = round(amount, 2)
        row["ledger_id"] = _coerce_ledger_id(row.get("ledger_id"))
        return row
    return None


def restore_backup(data: dict, replace: bool = False) -> dict:
    """从备份字典恢复数据，返回各节实际入库条数

    结构非法抛 ValidationError；单行非法跳过并计入 skipped。
    """
    if not isinstance(data, dict):
        raise ValidationError("备份文件格式不正确")
    if not any(isinstance(data.get(k), list) for k in _SECTIONS):
        raise ValidationError("备份文件缺少业务数据（categories/bills/budgets/assets）")

    parsed = {}
    skipped = {}
    for section in _SECTIONS:
        rows, bad = [], 0
        for raw in data.get(section) or []:
            cleaned = _clean_row(section, raw)
            if cleaned is None:
                bad += 1
            else:
                rows.append(cleaned)
        parsed[section] = rows
        skipped[section] = bad

    ledger_id_to_name = _ledger_id_to_name(data)

    with RESTORE_LOCK, get_db() as session:
        if replace:
            # 无外键约束，先清流水/预算/快照/账本/家庭再清分类（分类名被流水引用仅业务层面）
            for model in (
                Bill,
                Budget,
                AssetSnapshot,
                Ledger,
                Family,
                FamilyMember,
                Category,
            ):
                session.execute(delete(model))
        insert_ignore_rows(
            session.connection(),
            Category.__table__,
            parsed["categories"],
        )
        # 账本：按名去重插入（id 由目标库分配），随后按名把流水等重映射到新 id。
        # is_default 只是普通标记列（无唯一约束），备份可能带来第二个默认账本
        # （如默认账本被改过名的旧库备份合并进本库），故统一收敛：
        # - 合并模式先锁定本地默认账本，备份带来的默认标记让位（本地状态优先）；
        # - replace 模式库刚清空，以备份自带的 is_default 为准，无则新建默认账本；
        # - 插入后把选定默认之外的所有 is_default 标记降级，保证全局唯一默认。
        local_default = None if replace else ensure_default_ledger(session)
        insert_ignore_rows(session.connection(), Ledger.__table__, parsed["ledgers"])
        session.flush()
        default_ledger = local_default or ensure_default_ledger(session)
        session.execute(
            update(Ledger)
            .where(Ledger.is_default.is_(True), Ledger.id != default_ledger)
            .values(is_default=False)
            .execution_options(synchronize_session=False)
        )
        session.flush()
        ledger_name_to_id = {
            row.name: row.id for row in session.scalars(select(Ledger))
        }
        ledger_map = {
            old_id: ledger_name_to_id.get(name, default_ledger)
            for old_id, name in ledger_id_to_name.items()
        }
        for section in ("bills", "budgets", "assets"):
            for row in parsed[section]:
                row["ledger_id"] = ledger_map.get(row.get("ledger_id"), default_ledger)
        # 家庭：按邀请码去重插入（id 由目标库分配），成员行的 family_id 随后按
        # 码重映射到新 id；码在备份 families 节中找不到的家庭按坏行计 skipped
        # （合并模式下成员 user_id 已在本家庭时由唯一约束去重，保持本家庭归属）。
        insert_ignore_rows(session.connection(), Family.__table__, parsed["families"])
        session.flush()
        family_code_to_id = {
            row.invite_code: row.id for row in session.scalars(select(Family))
        }
        family_map = {
            old_id: family_code_to_id[code]
            for old_id, code in _family_id_to_code(data).items()
            if code in family_code_to_id
        }
        member_rows, orphan_members = [], 0
        for raw in data.get("family_members") or []:
            member = _clean_row("family_members", raw)
            if member is None:
                continue  # 坏行已在上方 parsed/skipped 统计
            target_id = family_map.get(_coerce_ledger_id(raw.get("family_id")))
            if target_id is None:
                orphan_members += 1
                continue
            member["family_id"] = target_id
            member_rows.append(member)
        parsed["family_members"] = member_rows
        skipped["family_members_orphan"] = orphan_members
        insert_ignore_rows(session.connection(), FamilyMember.__table__, member_rows)
        # 恢复流水前确保引用的分类存在（备份缺 categories 节时按流水补建）
        used_categories = sorted(
            {row["category"] for row in parsed["bills"] if row.get("category")}
        )
        insert_ignore_rows(
            session.connection(),
            Category.__table__,
            [{"name": n} for n in used_categories],
        )
        insert_ignore_rows(session.connection(), Bill.__table__, parsed["bills"])
        # 家庭预算（T-7.3）：family_id 按邀请码重映射到新 id，user_id 改写为
        # 合成属主（家庭行的 user_id 只是唯一键作用域，不承载归属语义）；
        # 映射不到家庭（备份 families 节缺失/坏行）的预算按孤儿跳过
        family_budget_rows, family_budget_orphan = [], 0
        for row in parsed["budgets"]:
            raw_family_id = row.pop("family_id", None)
            if raw_family_id is None:
                family_budget_rows.append(row)
                continue
            target_id = family_map.get(raw_family_id)
            if target_id is None:
                family_budget_orphan += 1
                continue
            row["family_id"] = target_id
            row["user_id"] = family_scope_user(target_id)
            family_budget_rows.append(row)
        parsed["budgets"] = family_budget_rows
        skipped["budgets_family_orphan"] = family_budget_orphan
        insert_ignore_rows(session.connection(), Budget.__table__, parsed["budgets"])
        insert_ignore_rows(
            session.connection(), AssetSnapshot.__table__, parsed["assets"]
        )
        session.flush()

    result = {
        "replaced": replace,
        "categories": len(parsed["categories"]),
        "ledgers": len(parsed["ledgers"]),
        "families": len(parsed["families"]),
        "family_members": len(parsed["family_members"]),
        "bills": len(parsed["bills"]),
        "budgets": len(parsed["budgets"]),
        "assets": len(parsed["assets"]),
        "skipped": sum(skipped.values()),
    }
    logger.info(
        "备份恢复完成（replace=%s）：流水 %s、分类 %s、账本 %s、家庭 %s、"
        "预算 %s、资产快照 %s、跳过 %s",
        replace,
        result["bills"],
        result["categories"],
        result["ledgers"],
        result["families"],
        result["budgets"],
        result["assets"],
        result["skipped"],
    )
    return result


def load_backup_text(raw: bytes) -> dict:
    """解析上传的备份文件字节为 JSON；失败抛 ValidationError"""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("备份文件不是 UTF-8 编码") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError("备份文件不是有效 JSON") from exc
