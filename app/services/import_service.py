"""账单导入业务：解析 → 自动归类 → 分类自动创建 → 去重入库"""
from fastapi import HTTPException, UploadFile

from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from app.parsers.base import BaseParser
from app.schemas.upload import ImportResult
from app.utils.category_matcher import match_category
from app.utils.file_utils import save_upload


def import_bill_file(file: UploadFile, parser: BaseParser, allowed_ext: str) -> ImportResult:
    """保存上传文件 → 解析 → 关键词自动归类 → 分类自动创建 → 事务去重入库，返回导入统计"""
    path = save_upload(file, allowed_ext)
    try:
        records = parser.parse(path)
        normalized = []
        for rec in records:
            rec["category"] = rec.get("category") or match_category(
                rec.get("merchant", ""), rec.get("remark", "")
            )
            normalized.append(rec)
        # 分类自动创建：导入涉及的分类不存在时自动写入 categories 表（幂等）
        CategoryDAO.ensure_many([r["category"] for r in normalized])
        inserted = BillDAO.insert_many(normalized)
        skipped = max(0, len(records) - inserted)
        return ImportResult(total=len(records), inserted=inserted, skipped=skipped)
    except HTTPException:
        raise
    except Exception as exc:  # 解析/入库异常统一转为 400，避免脏数据半入库
        raise HTTPException(status_code=400, detail=f"账单解析失败：{exc}") from exc
    finally:
        path.unlink(missing_ok=True)
