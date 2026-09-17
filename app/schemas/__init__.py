"""Pydantic 请求/响应模型。

各业务域模型在子模块中定义（bill/category/common/stat/upload/...），
消费方一律直接从子模块导入（如 `from app.schemas.bill import BillCreate`），
本包不再聚合再导出。
"""
