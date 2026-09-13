"""账单导入结果模型"""

from pydantic import BaseModel, Field


class ImportDetail(BaseModel):
    """导入差异明细条目：标识来源流水并说明差异原因

    tx_id 为空时（流水无交易号）以 merchant + amount 辅助定位。
    """

    tx_id: str = ""
    merchant: str = ""
    amount: float = 0
    reason: str


class ImportResult(BaseModel):
    """导入差异报告（T-5.5）

    total 解析到的流水数；inserted 实际新增；skipped_dup 重复跳过
    （交易号已存在）；details 为逐条差异原因；ai_classified 为智能分类
    （DeepSeek）二次归类的条数（未启用时为 0）。

    failed 与 unrecognized 为目录批量导入（监听/整目录上传）场景的统计
    字段，单文件导入时恒为 0，前端按需展示，勿删。
    skipped 为兼容字段 = skipped_dup（旧前端/旧调用方沿用）。
    """

    total: int
    inserted: int
    skipped: int
    ai_classified: int = 0
    skipped_dup: int = 0
    failed: int = 0
    unrecognized: int = 0
    details: list[ImportDetail] = Field(default_factory=list)
