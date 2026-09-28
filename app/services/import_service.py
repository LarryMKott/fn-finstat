"""账单导入业务：解析 → 自动归类（关键词+可选 AI）→ 分类自动创建 → 去重入库

导入全程写运行日志（设置页可查看）：开始、解析/归类/入库统计与失败原因。
上传导入与 NAS 目录导入共用 import_local_file 管线，仅文件来源不同。

异常约定：
    ValidationError   文件内容问题（解析失败），HTTP 400
    EnvironmentError_ 环境问题（驱动缺失/连接错误），HTTP 500
    其余未预期异常    由全局异常处理器统一转 500（不伪装成解析失败）
"""

import csv
import io
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import String
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import (
    BizError,
    EnvironmentError_,
    ImportParseError,
    ValidationError,
)
from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from app.db.models import Bill
from app.parsers.base import BaseParser
from app.schemas.upload import ImportDetail, ImportResult
from app.services import (
    ai_service,
    backup_service,
    keyword_service,
    learned_rule_service,
    scheduler,
)
from app.services.export_service import csv_safe
from app.utils.file_utils import save_upload

logger = logging.getLogger(__name__)

# 单文件导入的记录数上限：xlsx 是 zip 容器，10MB 大小上限可解出百万行 XML，
# 全量解析进内存会让 NAS 进程 OOM；正常年度账单远低于此值，超限提示拆分导入
MAX_IMPORT_RECORDS = 50_000


def _bill_text_limits() -> dict[str, int]:
    """bills 表文本列宽（models.py 为单一来源），导入侧按此统一截断"""
    return {
        c.name: int(c.type.length)
        for c in Bill.__table__.columns
        if isinstance(c.type, String) and c.type.length
    }


_BILL_TEXT_LIMITS = _bill_text_limits()


def _clip_text(value, limit: int):
    """按列宽截断超长文本；None 原样保留（空交易号转 NULL 的既有语义）

    不截断的后果：PG 超长 varchar 整批报错（且被误报为「数据库暂时不可用」）、
    MySQL 静默截断、SQLite 全收 —— 同一文件三库行为不一致，截断出的脏数据
    还会让后续迁移到 PG 的整库搬移失败。
    """
    if value is None:
        return None
    text = str(value)
    return text[:limit] if len(text) > limit else text


def import_local_file(
    path: Path, parser: BaseParser, display_name: str, user_id: str
) -> ImportResult:
    """解析本地账单文件 → 关键词归类 → AI 二次归类（可选）→ 分类自动创建 → 去重入库

    上传导入与 NAS 目录导入共用的核心管线；只读源文件，不负责删除。
    """
    logger.info(
        "账号 %s 开始导入 %s（解析器 %s）",
        user_id or "本地",
        display_name,
        parser.__class__.__name__,
    )
    # 恢复备份（尤其 replace 模式）期间并发导入会互相穿插写入：新提交的流水
    # 「穿越」清空点残留，最终状态既非纯备份也非纯现况。恢复独占本锁；
    # 导入以非阻塞方式尝试获取，恢复进行中直接拒绝并提示。
    if not backup_service.RESTORE_LOCK.acquire(blocking=False):
        raise ValidationError("正在恢复备份数据，请稍后再试")
    try:
        return _import_locked(path, parser, display_name, user_id)
    finally:
        backup_service.RESTORE_LOCK.release()


def _import_locked(
    path: Path, parser: BaseParser, display_name: str, user_id: str
) -> ImportResult:
    records, normalized, ai_classified = _parse_and_normalize(
        parser, path, display_name
    )
    try:
        fresh, dup_details = _split_fresh_and_dups(normalized)
        CategoryDAO.ensure_many([r["category"] for r in fresh])
        inserted = BillDAO.insert_many(fresh, user_id)
    except SQLAlchemyError as exc:
        # 数据库层故障（连接断开/参数超限等）是服务端问题，不能报成"文件解析失败"
        logger.error("账单导入失败（%s，数据库故障）：%s", display_name, exc)
        raise EnvironmentError_("数据库暂时不可用，请稍后重试") from exc
    skipped_dup = len(dup_details)
    logger.info(
        "账单导入完成：%s 解析 %s 条，新增 %s 条，重复跳过 %s 条，AI 归类 %s 条",
        display_name,
        len(records),
        inserted,
        skipped_dup,
        ai_classified,
    )
    return ImportResult(
        total=len(records),
        inserted=inserted,
        skipped=skipped_dup,
        ai_classified=ai_classified,
        skipped_dup=skipped_dup,
        details=dup_details,
    )


def _parse_and_normalize(
    parser: BaseParser, path: Path, display_name: str
) -> tuple[list[dict], list[dict], int]:
    """解析 + 逐行归一化 + AI 二次归类（文件内容敏感阶段）

    本阶段的意外异常视为文件内容问题转 ImportParseError（HTTP 400）；
    环境问题（RuntimeError）与数据库故障单独识别，不伪装成解析失败。
    """
    try:
        records = parser.parse(path)
        if len(records) > MAX_IMPORT_RECORDS:
            raise ImportParseError(
                f"账单条数 {len(records)} 超过单文件上限 {MAX_IMPORT_RECORDS}，"
                "请拆分后分批导入"
            )
        normalized = []
        for rec in records:
            # 文本列按数据库列宽截断（merchant/remark/tx_id/category/tags 等）
            for field, width in _BILL_TEXT_LIMITS.items():
                if field in rec:
                    rec[field] = _clip_text(rec[field], width)
            normalized.append(rec)
        # 分类优先级（T-6.3 + v1.1）：已学习规则 > 分类关键词表 > LLM。命中学习
        # 规则的流水覆盖解析器自带分类（用户纠正过两次的商户以纠正为准），且不再
        # 进入关键词匹配与 AI 二次归类；规则加载失败按无规则处理，不阻塞导入。
        learned_rule_service.apply_to_records(normalized)
        # 关键词层读 category_keywords 表（内置 RULES 已播种进表，见 keyword_seed），
        # 表故障时逐条回退内置 RULES 硬匹配，归类行为永不中断
        keyword_service.apply_to_records(normalized)
        # 智能分类（设置页启用时）：关键词未命中的"其他"流水交给 DeepSeek 语义归类，
        # 失败或未配置只跳过、不影响导入
        ai_classified = ai_service.enhance_import_records(normalized)
        return records, normalized, ai_classified
    except BizError:
        # 业务异常（唯一冲突/校验失败等）保持原语义，不伪装成解析失败
        raise
    except RuntimeError as exc:  # 驱动缺失/连接配置错误等环境问题，不是解析失败
        logger.error("账单导入失败（%s，环境问题）：%s", display_name, exc)
        raise EnvironmentError_(str(exc)) from exc
    except SQLAlchemyError as exc:
        logger.error("账单导入失败（%s，数据库故障）：%s", display_name, exc)
        raise EnvironmentError_("数据库暂时不可用，请稍后重试") from exc
    except Exception as exc:  # 解析/归一化异常统一转为 400
        logger.warning("账单导入失败（%s，文件内容问题）：%s", display_name, exc)
        # 底层异常原文（openpyxl / pandas / OS）常含 TMP_DIR、账单目录等内部
        # 绝对路径，回显给用户前统一脱敏（与调度器任务消息同一口径）
        raise ImportParseError(
            f"账单解析失败：{scheduler.redact_paths(str(exc))}"
        ) from exc


def _split_fresh_and_dups(
    normalized: list[dict],
) -> tuple[list[dict], list[ImportDetail]]:
    """差异报告（T-5.5）：入库前精确区分新增/重复，重复条目带逐条原因

    只把新流水交给入库（与唯一约束兜底的双保险，避免整批 IGNORE 的黑盒跳过）。
    批内也要去重：同一文件出现两次相同交易号时只保留首条，其余计入
    skipped_dup —— 否则 total = inserted + skipped_dup 的差异报告等式不成立
    （insert_ignore 只会插入一条，另一条静默消失）。
    """
    existing = BillDAO.existing_tx_ids(
        [r.get("tx_id") for r in normalized if r.get("tx_id")]
    )
    fresh = []
    dup_details = []
    seen: set[str] = set()

    def _dup_detail(r: dict, reason: str) -> ImportDetail:
        return ImportDetail(
            tx_id=r.get("tx_id") or "",
            merchant=r.get("merchant", ""),
            amount=float(r.get("amount") or 0),
            reason=reason,
        )

    for r in normalized:
        tx_id = r.get("tx_id")
        if not tx_id:
            fresh.append(r)  # 无交易号的流水不受唯一约束，全部入库
            continue
        if tx_id in existing:
            dup_details.append(_dup_detail(r, "交易号已存在，重复跳过"))
            continue
        if tx_id in seen:
            dup_details.append(_dup_detail(r, "同一文件内交易号重复，只保留首条"))
            continue
        seen.add(tx_id)
        fresh.append(r)
    return fresh, dup_details


def import_bill_file(
    file, parser: BaseParser, allowed_ext: str, user_id: str
) -> ImportResult:
    """保存上传文件 → 走通用导入管线 → 清理临时文件

    parser 由 api 层按平台注册表（app/parsers.PARSERS）实例化后传入。
    """
    path = save_upload(file, allowed_ext)
    try:
        return import_local_file(path, parser, file.filename or "上传文件", user_id)
    finally:
        path.unlink(missing_ok=True)


def export_details(details: list[ImportDetail]) -> tuple[str, bytes, str]:
    """导出导入差异报告为 CSV（REQ-ING-005「可导出」）

    差异报告是导入时的临时结果，不落库；此处把前端已拿到的 details 转为 CSV。
    复用 export_service.csv_safe 防公式注入。
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["交易单号", "商户", "金额(元)", "原因"])
    for d in details:
        writer.writerow(
            [csv_safe(d.tx_id), csv_safe(d.merchant), d.amount, csv_safe(d.reason)]
        )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"import-diff-{stamp}.csv"
    content = buf.getvalue().encode("utf-8-sig")
    return filename, content, "text/csv; charset=utf-8"
