"""账单导入结果模型"""
from pydantic import BaseModel


class ImportResult(BaseModel):
    total: int
    inserted: int
    skipped: int
