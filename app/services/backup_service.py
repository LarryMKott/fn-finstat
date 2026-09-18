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
"""

import json
import logging
import threading
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, select

from app.core.errors import ValidationError
from app.db.base import LATEST_SCHEMA_VERSION, get_db, insert_ignore_rows
from app.db.ledgers import ensure_default_ledger
from app.db.models import AssetSnapshot, Bill, Budget, Category, Ledger

logger = logging.getLogger(__name__)

BACKUP_FORMAT_VERSION = 2  # v2：新增 ledgers 节与 ledger_id 字段（T-7.1）

# 恢复与导入共用一把进程级互斥锁：恢复（尤其 replace 模式）期间并发导入的
# 写入会「穿越」清空点残留，最终库状态既非纯备份也非纯现况。恢复侧独占；
# 导入侧（import_service.import_local_file）以非阻塞方式尝试获取，恢复进行中
# 直接拒绝新导入并提示用户。
RESTORE_LOCK = threading.Lock()

# 备份文件键 → ORM 模型（导出与恢复共用）
_SECTIONS = {
    "categories": Category,
    "ledgers": Ledger,
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
    "budgets": {"user_id", "ledger_id", "month", "category", "amount"},
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

VALID_TX_TYPES = {"expense", "income", "transfer"}


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


def export_backup() -> dict:
    """导出全库业务数据为可 JSON 序列化的字典"""
    data = {
        "app": "fn-finstat",
        "format_version": BACKUP_FORMAT_VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "schema_version": LATEST_SCHEMA_VERSION,
    }
    with get_db() as session:
        data["categories"] = [c.name for c in session.scalars(select(Category))]
        # 账本保留 id：恢复时据此把流水/预算/快照的 ledger_id 按名重映射到新 id
        data["ledgers"] = [l.as_dict() for l in session.scalars(select(Ledger))]
        data["bills"] = [b.as_dict() for b in session.scalars(select(Bill))]
        data["budgets"] = [b.as_dict() for b in session.scalars(select(Budget))]
        data["assets"] = [a.as_dict() for a in session.scalars(select(AssetSnapshot))]
    # 流水的 id 由目标库自增，不导出
    for bill in data["bills"]:
        bill.pop("id", None)
    for section in ("budgets", "assets"):
        for row in data[section]:
            row.pop("id", None)
    return data


def _clean_row(section: str, raw: dict) -> Optional[dict]:
    """按白名单字段清洗一行数据；类型不合法返回 None（恢复时跳过）"""
    if not isinstance(raw, dict):
        return None
    row = {k: raw.get(k) for k in _FIELDS[section]}
    if section == "categories":
        name = str(row.get("name") or "").strip()
        return {"name": name[:64]} if name else None
    if section == "bills":
        if not str(row.get("tx_time") or "").strip():
            return None
        if str(row.get("tx_type")) not in VALID_TX_TYPES:
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
    if section == "budgets":
        try:
            amount = float(row.get("amount") or 0)
        except (TypeError, ValueError):
            return None
        if amount <= 0:
            return None
        row["amount"] = round(amount, 2)
        row["ledger_id"] = _coerce_ledger_id(row.get("ledger_id"))
        return row
    if section == "assets":
        try:
            amount = float(row.get("amount") or 0)
        except (TypeError, ValueError):
            return None
        if amount < 0:
            return None
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
            # 无外键约束，先清流水/预算/快照/账本再清分类（分类名被流水引用仅业务层面）
            for model in (Bill, Budget, AssetSnapshot, Ledger, Category):
                session.execute(delete(model))
        insert_ignore_rows(
            session.connection(),
            Category.__table__,
            parsed["categories"],
        )
        # 账本：按名去重插入（id 由目标库分配），随后按名把流水等重映射到新 id
        insert_ignore_rows(session.connection(), Ledger.__table__, parsed["ledgers"])
        session.flush()
        default_ledger = ensure_default_ledger(session)
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
        insert_ignore_rows(session.connection(), Budget.__table__, parsed["budgets"])
        insert_ignore_rows(
            session.connection(), AssetSnapshot.__table__, parsed["assets"]
        )
        session.flush()

    result = {
        "replaced": replace,
        "categories": len(parsed["categories"]),
        "ledgers": len(parsed["ledgers"]),
        "bills": len(parsed["bills"]),
        "budgets": len(parsed["budgets"]),
        "assets": len(parsed["assets"]),
        "skipped": sum(skipped.values()),
    }
    logger.info(
        "备份恢复完成（replace=%s）：流水 %s、分类 %s、账本 %s、预算 %s、"
        "资产快照 %s、跳过 %s",
        replace,
        result["bills"],
        result["categories"],
        result["ledgers"],
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
