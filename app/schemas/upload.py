"""账单导入结果模型"""
from pydantic import BaseModel


class ImportResult(BaseModel):
    """导入统计：total 解析到的流水数，inserted 实际新增，skipped 因重复跳过"""

    total: int
    inserted: int
    skipped: int
