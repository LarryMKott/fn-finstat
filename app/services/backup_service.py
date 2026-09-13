"""全量数据备份与恢复（JSON 文件，兼容 SQLite / MySQL / PostgreSQL）

- 备份：导出全部账号的 分类 / 流水 / 预算 / 资产快照 为一个 JSON 文件下载
- 恢复：
    replace=False（默认）合并模式——按唯一键去重导入（流水 tx_id、分类名、预算唯一键；
      无交易号的流水无法去重，重复恢复同一备份可能产生重复记录）
    replace=True 覆盖模式——先清空全部业务表再导入（不可恢复，前端需二次确认）
- 备份文件不包含数据库连接配置与 AI/日志等运行配置，仅业务数据
"""

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.db.base import LATEST_SCHEMA_VERSION, get_db, insert_ignore_rows
from app.db.models import AssetSnapshot, Bill, Budget, Category

logger = logging.getLogger(__name__)

BACKUP_FORMAT_VERSION = 1

# 备份文件键 → ORM 模型（导出与恢复共用）
_SECTIONS = {
    "categories": Category,
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
    },
    "budgets": {"user_id", "month", "category", "amount"},
    "assets": {"user_id", "snap_date", "name", "asset_type", "amount", "remark"},
}

VALID_TX_TYPES = {"expense", "income", "transfer"}


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
        return row
    if section == "budgets":
        try:
            amount = float(row.get("amount") or 0)
        except (TypeError, ValueError):
            return None
        if amount <= 0:
            return None
        row["amount"] = round(amount, 2)
        return row
    if section == "assets":
        try:
            amount = float(row.get("amount") or 0)
        except (TypeError, ValueError):
            return None
        if amount < 0:
            return None
        row["amount"] = round(amount, 2)
        return row
    return None


def restore_backup(data: dict, replace: bool = False) -> dict:
    """从备份字典恢复数据，返回各节实际入库条数

    结构非法抛 HTTPException(400)；单行非法跳过并计入 skipped。
    """
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="备份文件格式不正确")
    if not any(isinstance(data.get(k), list) for k in _SECTIONS):
        raise HTTPException(
            status_code=400,
            detail="备份文件缺少业务数据（categories/bills/budgets/assets）",
        )

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

    with get_db() as session:
        if replace:
            # 无外键约束，先清流水/预算/快照再清分类（分类名被流水引用仅业务层面）
            for model in (Bill, Budget, AssetSnapshot, Category):
                session.execute(delete(model))
        insert_ignore_rows(
            session.connection(),
            Category.__table__,
            parsed["categories"],
        )
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
        "bills": len(parsed["bills"]),
        "budgets": len(parsed["budgets"]),
        "assets": len(parsed["assets"]),
        "skipped": sum(skipped.values()),
    }
    logger.info(
        "备份恢复完成（replace=%s）：流水 %s、分类 %s、预算 %s、资产快照 %s、跳过 %s",
        replace,
        result["bills"],
        result["categories"],
        result["budgets"],
        result["assets"],
        result["skipped"],
    )
    return result


def load_backup_text(raw: bytes) -> dict:
    """解析上传的备份文件字节为 JSON；失败抛 HTTPException(400)"""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="备份文件不是 UTF-8 编码") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="备份文件不是有效 JSON") from exc
