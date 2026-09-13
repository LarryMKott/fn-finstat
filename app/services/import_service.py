"""账单导入业务：解析 → 自动归类（关键词+可选 AI）→ 分类自动创建 → 去重入库

导入全程写运行日志（设置页可查看）：开始、解析/归类/入库统计与失败原因。
上传导入与 NAS 目录导入共用 import_local_file 管线，仅文件来源不同。
"""

import logging
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from app.parsers.base import BaseParser
from app.schemas.upload import ImportResult
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
        # 分类自动创建：导入涉及的分类不存在时自动写入 categories 表（幂等）
        CategoryDAO.ensure_many([r["category"] for r in normalized])
        inserted = BillDAO.insert_many(normalized, user_id)
        skipped = max(0, len(records) - inserted)
        logger.info(
            "账单导入完成：%s 解析 %s 条，新增 %s 条，重复跳过 %s 条，AI 归类 %s 条",
            display_name,
            len(records),
            inserted,
            skipped,
            ai_classified,
        )
        return ImportResult(
            total=len(records),
            inserted=inserted,
            skipped=skipped,
            ai_classified=ai_classified,
        )
    except HTTPException:
        raise
    except RuntimeError as exc:  # 驱动缺失/连接配置错误等环境问题，不是解析失败
        logger.error("账单导入失败（%s，环境问题）：%s", display_name, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # 解析/入库异常统一转为 400，避免脏数据半入库
        logger.warning("账单导入失败（%s，文件内容问题）：%s", display_name, exc)
        raise HTTPException(status_code=400, detail=f"账单解析失败：{exc}") from exc


def import_bill_file(
    file: UploadFile, parser: BaseParser, allowed_ext: str, user_id: str
) -> ImportResult:
    """保存上传文件 → 走通用导入管线 → 清理临时文件"""
    path = save_upload(file, allowed_ext)
    try:
        return import_local_file(path, parser, file.filename or "上传文件", user_id)
    finally:
        path.unlink(missing_ok=True)
