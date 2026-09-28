"""分类关键词服务（v1.1 CAP-1）：关键词表的维护 + 导入匹配第二层

归类链优先级（与 T-6.3 口径衔接）：已学习规则 > 分类关键词（本表）> LLM。
- 内置 category_matcher.RULES 播种进关键词表后退出匹配链（keyword_seed），
  本表是关键词匹配的唯一数据源：内置词在界面可见、可停用、可删除
- 多词命中按「keyword 更长者优先」（DAO 已按长度降序返回），更具体的商户名
  优先于宽泛词；同长按 id 先到先得
- 匹配只读表内启用行（子串、大小写不敏感，与 learned_rules 同口径）；表
  加载失败回退内置 RULES 硬匹配——归类永不因本表故障中断
"""

import logging
import re

from app.config import DEFAULT_CATEGORY
from app.core.errors import NotFoundError, ValidationError
from app.db.dao.category_dao import CategoryDAO
from app.db.dao.category_keyword_dao import CategoryKeywordDAO
from app.utils import category_matcher

logger = logging.getLogger(__name__)

# 关键词长度边界（与 learned_rule_service 的 pattern 同口径）：过短泛化风险
# 高（如「购」会命中全库），过长超出列宽
KEYWORD_MIN_LENGTH = 2
KEYWORD_MAX_LENGTH = 64
# 单次入库上限（AI apply / 人工批量粘贴的护栏）
KEYWORD_BATCH_LIMIT = 100

# 非法关键词：含空白或控制字符（匹配按子串进行，带空格的词几乎必然是拆分错误）
_BAD_KEYWORD = re.compile(r"[\s\u0000-\u001f\u007f]")


def is_valid_keyword(word: str) -> bool:
    """单条关键词合法性：去首尾空白后长度达标且不含空白/控制字符"""
    word = str(word or "").strip()
    if not KEYWORD_MIN_LENGTH <= len(word) <= KEYWORD_MAX_LENGTH:
        return False
    return _BAD_KEYWORD.search(word) is None


def clean_keywords(raw: list) -> tuple[list[str], int]:
    """清洗关键词候选，返回 (合格词列表保持原顺序, 剔除条数)

    剔除口径：空值、长度越界、含空白/控制字符；批内去重（大小写不敏感，保留
    首个写法）。与既有词的重复不在本步剔除——由入库的唯一键兜住并计数。
    """
    valid: list[str] = []
    seen: set[str] = set()
    dropped = 0
    for item in raw or []:
        word = str(item or "").strip()
        if not is_valid_keyword(word):
            dropped += 1
            continue
        if word.lower() in seen:
            continue
        seen.add(word.lower())
        valid.append(word)
    return valid, dropped


# ---- 匹配（导入链第二层）----


def _load_snapshot() -> list[dict] | None:
    """启用关键词快照（长词优先）；表故障返回 None（调用方回退 RULES）"""
    try:
        return CategoryKeywordDAO.list_enabled_with_category()
    except Exception as exc:  # 数据库故障兜底：退回内置词，归类不中断
        logger.warning("关键词表加载失败（本次回退内置 RULES 匹配）：%s", exc)
        return None


def _hit(snapshot: list[dict], text: str) -> str | None:
    for row in snapshot:
        if row["keyword"].lower() in text:
            return row["category"]
    return None


def match(merchant: str, remark: str = "") -> str:
    """关键词匹配单条（子串、大小写不敏感），未命中返回默认分类「其他」"""
    text = f"{merchant} {remark}".lower()
    snapshot = _load_snapshot()
    if snapshot is None:
        return category_matcher.match_category(merchant, remark)
    return _hit(snapshot, text) or DEFAULT_CATEGORY


def apply_to_records(records: list[dict]) -> int:
    """导入归类第二层：对分类为空的流水应用关键词表（原地改写 category）

    语义与被替代的 category_matcher 循环逐字一致：只填空分类，未命中落
    「其他」；命中后不再进入 AI 二次归类（其只处理「其他」）。返回命中条数。
    """
    if not records:
        return 0
    snapshot = _load_snapshot()
    matched = 0
    for rec in records:
        if rec.get("category"):
            continue
        text = f"{rec.get('merchant') or ''} {rec.get('remark') or ''}".lower()
        if snapshot is None:  # 表故障：逐条回退内置 RULES
            rec["category"] = category_matcher.match_category(
                rec.get("merchant", ""), rec.get("remark", "")
            )
            continue
        category = _hit(snapshot, text)
        if category is not None:
            matched += 1
        rec["category"] = category or DEFAULT_CATEGORY
    if matched:
        logger.info("分类关键词归类 %s 条（优先于 AI 兜底）", matched)
    return matched


# ---- 管理接口（分类管理页关键词抽屉）----


def list_keywords(category_id: int) -> list[dict]:
    """某分类的关键词列表；分类不存在抛 404"""
    if CategoryDAO.get_by_id(category_id) is None:
        raise NotFoundError("分类不存在")
    return CategoryKeywordDAO.list_by_category(category_id)


def add_keywords(category_id: int, keywords: list, source: str = "manual") -> dict:
    """批量新增关键词，返回 {added, dropped, duplicated}（幂等，重名不报错）"""
    if CategoryDAO.get_by_id(category_id) is None:
        raise NotFoundError("分类不存在")
    valid, dropped = clean_keywords(list(keywords or [])[:KEYWORD_BATCH_LIMIT])
    if not valid:
        raise ValidationError(
            f"没有合法关键词（长度需 {KEYWORD_MIN_LENGTH}-{KEYWORD_MAX_LENGTH} 字，"
            "且不含空格）"
        )
    added = CategoryKeywordDAO.create_many(category_id, valid, source=source)
    return {
        "added": added,
        "dropped": dropped,
        "duplicated": len(valid) - added,
    }


def set_keyword_enabled(keyword_id: int, enabled: bool) -> dict:
    """停用/启用单条；不存在抛 404"""
    row = CategoryKeywordDAO.set_enabled(keyword_id, enabled)
    if row is None:
        raise NotFoundError("关键词不存在")
    return row


def delete_keyword(keyword_id: int) -> bool:
    """删除单条；不存在返回 False"""
    return CategoryKeywordDAO.delete(keyword_id)
