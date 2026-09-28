"""备注语义检索服务（AI-8）：零依赖 TF-IDF，突破关键词精确匹配

解决「上次给家里人买东西花了多少」这类模糊问法——查询词与备注往往
无字面重叠，关键词检索在该场景基本失效（Top10 命中 1/9，见脑洞清单 §7.4）。

技术路线（已拍板）：纯标准库 TF-IDF + 余弦相似度（app/utils/note_search），
检索域 = 商户 + 备注。明确不做：本地向量模型（≈1.5GB 违背零依赖红线）、
LLM 打标落库（API 费用 + 词表维护，留作 TF-IDF 不够时的第二阶段增强）。

口径约定：
- 相关度为 0~1 的相对排序分，无绝对语义（单字巧合也会给分），
  只用于排序展示，不做硬性过滤；
- 索引每次检索现场重建（几千条毫秒级），条数上限 NOTE_SEARCH_MAX_DOCS
  按 id 倒序取最近的，超出部分不参与检索（随响应回传 capped 供前端提示）。
"""

from typing import Optional

from app.core.errors import BizError, ErrorCode
from app.db.dao.bill_dao import BillDAO
from app.schemas.bill import BillOut
from app.utils.note_search import TfIdfIndex

# 参与索引的流水条数上限：纯 Python 建索引 O(总词数)，3000 条毫秒级；
# 按 id 倒序取最近的——旧流水对「找最近那笔」的检索价值也最低
NOTE_SEARCH_MAX_DOCS = 3000
DEFAULT_TOP_K = 10
MAX_TOP_K = 50

CALIBER_TOKENIZE = "中文单字+相邻双字+英文数字词（零依赖 TF-IDF，无分词库）"


def search_notes(
    user_id: str,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    tx_type: Optional[str] = None,
    ledger_id: Optional[int] = None,
) -> dict:
    """按语义相关度检索当前账号的流水（商户 + 备注域），返回 Top-K 与口径

    结果按相关度降序（同分按流水 id 稳定排序），行结构与 BillOut 一致
    （经模型过滤掉 user_id/deleted 等内部字段）并附加 score。
    """
    text = str(query or "").strip()
    if not text:
        raise BizError("检索词不能为空", code=ErrorCode.BILL_INVALID)
    try:
        top_k = int(top_k)
    except (TypeError, ValueError):
        top_k = DEFAULT_TOP_K
    top_k = max(1, min(top_k, MAX_TOP_K))

    rows = BillDAO.note_search_rows(
        user_id, tx_type=tx_type, ledger_id=ledger_id, limit=NOTE_SEARCH_MAX_DOCS
    )
    caliber = {
        "fields": "商户+备注",
        "max_docs": NOTE_SEARCH_MAX_DOCS,
        "top_k": top_k,
        "tokenize": CALIBER_TOKENIZE,
        "capped": len(rows) >= NOTE_SEARCH_MAX_DOCS,
    }
    if not rows:
        return {"query": text, "indexed": 0, "results": [], "caliber": caliber}

    index = TfIdfIndex([f"{r['merchant']} {r['remark']}" for r in rows])
    hits = index.search(text, top_k=top_k)
    if not hits:
        return {"query": text, "indexed": len(rows), "results": [], "caliber": caliber}

    hit_ids = [rows[idx]["id"] for idx, _ in hits]
    by_id = {r["id"]: r for r in BillDAO.list_by_ids(hit_ids, user_id)}
    results: list[dict] = []
    for idx, score in hits:
        row = by_id.get(rows[idx]["id"])
        if row is None:
            continue
        item = BillOut(**row).model_dump()
        item["score"] = round(score, 4)
        results.append(item)
    return {"query": text, "indexed": len(rows), "results": results, "caliber": caliber}
