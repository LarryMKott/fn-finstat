"""操作审计服务（T-7.6）：业务写操作留痕与查询

- 打点：各 service 写操作成功后调用 record(...)；record 为 best-effort——
  审计失败只记 warning，绝不影响业务主流程
- 摘要：diff_summary 生成前后差异摘要（如「金额 10→20；分类 餐饮→交通」）
- 保留窗口：AUDIT_RETENTION_DAYS 天，写入时顺带清理过期行
- 查询：管理员可查全部账号（可按 user_id / action 过滤），普通账号强制
  只看自己的操作（服务端裁剪，与数据隔离同策略）
"""

import logging
import time

from app.db.dao.audit_dao import AuditDAO

logger = logging.getLogger(__name__)

# 审计保留窗口（天）：台账记录的是「谁在什么时候动了什么」，长期留存意义有限
AUDIT_RETENTION_DAYS = 90
# 单次查询上限
LIST_LIMIT_MAX = 1000


def record(
    user_id: str,
    action: str,
    entity: str = "",
    entity_id=None,
    summary: str = "",
) -> None:
    """写入一条审计（best-effort）：任何异常只记 warning，不影响业务主流程"""
    try:
        now = time.time()
        AuditDAO.create(
            user_id=user_id or "",
            action=action,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            summary=summary[:512],
            created_at=now,
        )
        cutoff = now - AUDIT_RETENTION_DAYS * 86400
        AuditDAO.purge_older_than(cutoff)
    except Exception:  # noqa: BLE001  审计是旁路能力，失败不阻塞业务
        logger.warning("操作审计写入失败（action=%s）", action, exc_info=True)


def diff_summary(before: dict | None, after: dict | None, fields: dict) -> str:
    """生成前后差异摘要：fields 为 {字段名: 中文标签}

    before / after 任一为 None 时输出「创建 / 删除」语义的基础信息；
    变更字段输出「标签 旧→新」，多个字段以「；」连接。
    """
    parts = []
    if before is None and after is not None:
        for key, label in fields.items():
            value = after.get(key)
            if value not in (None, ""):
                parts.append(f"{label} {value}")
        return "新增：" + "；".join(parts) if parts else "新增"
    if before is not None and after is None:
        for key, label in fields.items():
            value = before.get(key)
            if value not in (None, ""):
                parts.append(f"{label} {value}")
        return "删除：" + "；".join(parts) if parts else "删除"
    if before is not None and after is not None:
        for key, label in fields.items():
            old, new = before.get(key), after.get(key)
            if old != new and not (old in (None, "") and new in (None, "")):
                parts.append(f"{label} {old}→{new}")
        return "；".join(parts)
    return ""


def list_logs(
    user_id: str,
    is_admin: bool,
    limit: int = 200,
    offset: int = 0,
    action: str | None = None,
    target_user_id: str | None = None,
) -> dict:
    """查询审计：管理员可查全部账号（可按 user_id / action 过滤），
    普通账号强制只看自己的操作（user_id 过滤由服务端强制注入，
    与业务数据的隔离策略一致）"""
    limit = max(1, min(int(limit), LIST_LIMIT_MAX))
    offset = max(0, int(offset))
    query_user = user_id if not is_admin else (target_user_id or None)
    total, rows = AuditDAO.list_logs(query_user, action or None, limit, offset)
    return {"total": total, "items": rows}
