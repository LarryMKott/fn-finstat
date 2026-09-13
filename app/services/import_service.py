"""账单导入业务：解析 → 自动归类（关键词+可选 AI）→ 分类自动创建 → 去重入库

导入全程写运行日志（设置页可查看）：开始、解析/归类/入库统计与失败原因。
上传导入与 NAS 目录导入共用 import_local_file 管线，仅文件来源不同。

异常约定：
    ValidationError   文件内容问题（解析失败），HTTP 400
    EnvironmentError_ 环境问题（驱动缺失/连接错误），HTTP 500
    其余未预期异常    由全局异常处理器统一转 500（不伪装成解析失败）
"""

import logging
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import BizError, EnvironmentError_, ImportParseError
from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from app.parsers.base import BaseParser
from app.schemas.upload import ImportDetail, ImportResult
from app.services import ai_service
from app.utils.category_matcher import match_category
from app.utils.file_utils import save_upload

logger = logging.getLogger(__name__)


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
    try:
        records = parser.parse(path)
        normalized = []
        for rec in records:
            rec["category"] = rec.get("category") or match_category(
                rec.get("merchant", ""), rec.get("remark", "")
            )
            normalized.append(rec)
        # 智能分类（设置页启用时）：关键词未命中的"其他"流水交给 DeepSeek 语义归类，
        # 失败或未配置只跳过、不影响导入
        ai_classified = ai_service.enhance_import_records(normalized)
        # 差异报告（T-5.5）：入库前精确区分新增/重复，重复条目带逐条原因，
        # 只把新流水交给入库（与唯一约束兜底的双保险，避免整批 IGNORE 的黑盒跳过）
        existing = BillDAO.existing_tx_ids(
            [r.get("tx_id") for r in normalized if r.get("tx_id")]
        )
        # 批内也要去重：同一文件出现两次相同交易号时只保留首条，其余计入
        # skipped_dup —— 否则 total = inserted + skipped_dup 的差异报告等式不成立
        # （insert_ignore 只会插入一条，另一条静默消失）
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
        CategoryDAO.ensure_many([r["category"] for r in fresh])
        inserted = BillDAO.insert_many(fresh, user_id)
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
    except BizError:
        # 业务异常（唯一冲突/校验失败等）保持原语义，不伪装成解析失败
        raise
    except RuntimeError as exc:  # 驱动缺失/连接配置错误等环境问题，不是解析失败
        logger.error("账单导入失败（%s，环境问题）：%s", display_name, exc)
        raise EnvironmentError_(str(exc)) from exc
    except SQLAlchemyError as exc:
        # 数据库层故障（连接断开/参数超限等）是服务端问题，不能报成"文件解析失败"
        logger.error("账单导入失败（%s，数据库故障）：%s", display_name, exc)
        raise EnvironmentError_("数据库暂时不可用，请稍后重试") from exc
    except Exception as exc:  # 解析异常统一转为 400，避免脏数据半入库
        logger.warning("账单导入失败（%s，文件内容问题）：%s", display_name, exc)
        raise ImportParseError(f"账单解析失败：{exc}") from exc


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
