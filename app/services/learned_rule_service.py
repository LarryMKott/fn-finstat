"""分类规则自学习服务（T-6.3）：从用户手动纠正中沉淀「商户关键词 → 分类」规则

归类优先级（明确顺序）：**已学习规则 > 内置关键词（category_matcher）> LLM**。

- 触发：用户手动修改流水分类（单条编辑 / 批量设置分类）时，抽取商户关键词生成
  候选规则；同一 (pattern, category) 累计 **2 次**纠正才参与匹配——避免一次误改
  就污染全库。
- 冲突：同一 pattern 指向不同分类时取 hits 最高者；用户再次纠正会累加对应方向
  的证据并自动恢复启用，纠正互相抵消时（各 1 次）都不生效。
- 匹配：多个 pattern 同时命中商户名时按「pattern 更长者优先」（更具体的商户名
  优先于宽泛词），大小写不敏感（与内置关键词匹配同口径）。
- 边界：规则全局共享（分类全局共用，与内置关键词/AI 通道同口径）；学习/匹配
  任何异常只记日志，绝不影响记账与导入主流程。
"""

import logging
import re

from app.config import DEFAULT_CATEGORY
from app.core.errors import NotFoundError, ValidationError
from app.db.dao.category_dao import CategoryDAO
from app.db.dao.learned_rule_dao import LearnedRuleDAO
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

# 同一纠正方向累计达到该次数才参与匹配（REQ-AIC-001：改 2 次、第 3 笔起生效）
CONFIRM_THRESHOLD = 2

# 提取 pattern 时剔除的括号片段（门店号/订单尾缀等）：「XX（朝阳门店）」→「XX」
_BRACKET_SEGMENTS = re.compile(
    r"[（(【\[][^\s（）()【\]】\]]*[）)】\]]|[「『][^「」『』]*[」』]"
)
# 首尾需要剥掉的装饰字符（空格/连字符/间隔点等）
_EDGE_NOISE = " \t\u3000-_—–·.。/\\"
# pattern 长度边界：过短泛化风险高，过长超出列宽（String(64)）
PATTERN_MIN_LENGTH = 2
PATTERN_MAX_LENGTH = 64


def extract_pattern(merchant: str) -> str:
    """从商户名提取规则关键词：剔括号片段 → 剥首尾装饰 → 长度边界裁剪

    提取不出合格 pattern（空商户/过短/全装饰字符）返回空串，调用方跳过学习。
    """
    text = str(merchant or "").strip()
    text = _BRACKET_SEGMENTS.sub("", text)
    text = text.strip(_EDGE_NOISE)
    if len(text) < PATTERN_MIN_LENGTH:
        return ""
    return text[:PATTERN_MAX_LENGTH]


def record_correction(merchant: str, category: str) -> dict | None:
    """登记一次手动纠正证据：(pattern, category) 存在则 hits+1，否则建候选行

    纠正到默认分类「其他」不产生证据（那是未归类，不是学习信号）。
    本函数服务于记账主流程的钩子：任何异常只记日志、绝不向上抛。
    """
    category = (category or "").strip()
    if not category or category == DEFAULT_CATEGORY:
        return None
    pattern = extract_pattern(merchant)
    if not pattern:
        return None
    try:
        existing = LearnedRuleDAO.find(pattern, category)
        if existing is None:
            try:
                rule = LearnedRuleDAO.create(pattern, category)
            except SQLAlchemyError:
                # 并发下同一 (pattern, category) 撞唯一约束：按已存在行累加
                existing = LearnedRuleDAO.find(pattern, category)
                if existing is None:
                    raise
                rule = None
            if rule is not None:
                logger.info(
                    "学习规则候选建立：%s -> %s（1/%d）",
                    pattern,
                    category,
                    CONFIRM_THRESHOLD,
                )
                return rule
        if existing["hits"] < CONFIRM_THRESHOLD:
            logger.info(
                "学习规则证据累加：%s -> %s（%d/%d）",
                pattern,
                category,
                existing["hits"] + 1,
                CONFIRM_THRESHOLD,
            )
        if not existing["enabled"]:
            # 用户再次纠正同一方向 = 推翻手动停用，恢复启用
            LearnedRuleDAO.re_enable(existing["id"])
        LearnedRuleDAO.increment_hits(existing["id"])
        return LearnedRuleDAO.get(existing["id"])
    except Exception as exc:  # 学习失败绝不影响记账主流程
        logger.warning("学习规则记录失败（已忽略）：%s", exc)
        return None


def _effective_rules() -> list[dict]:
    """参与匹配的生效规则：启用 + 证据达标 + 分类仍存在；同 pattern 取 hits 最高

    返回按 (pattern 长度降序, hits 降序, id 升序) 排好的列表——长度优先保证
    更具体的商户名先命中。
    """
    rules = [r for r in LearnedRuleDAO.list_enabled() if r["hits"] >= CONFIRM_THRESHOLD]
    if not rules:
        return []
    valid_categories = {c["name"] for c in CategoryDAO.list_all()}
    rules = [r for r in rules if r["category"] in valid_categories]
    best: dict[str, dict] = {}
    for rule in rules:
        incumbent = best.get(rule["pattern"])
        if incumbent is None or (-rule["hits"], rule["id"]) < (
            -incumbent["hits"],
            incumbent["id"],
        ):
            best[rule["pattern"]] = rule
    return sorted(
        best.values(), key=lambda r: (-len(r["pattern"]), -r["hits"], r["id"])
    )


def match(merchant: str) -> str | None:
    """商户名规则匹配（子串、大小写不敏感），未命中返回 None"""
    text = str(merchant or "").lower()
    if not text:
        return None
    for rule in _effective_rules():
        if rule["pattern"].lower() in text:
            return rule["category"]
    return None


def apply_to_records(records: list[dict]) -> int:
    """导入归类第一阶段：对整批流水应用已学习规则（原地改写 category）

    命中规则的流水不再走内置关键词与 LLM（优先级最高；AI 二次归类只处理
    「其他」分类，命中即天然跳过）。返回命中条数。
    """
    if not records:
        return 0
    try:
        rules = _effective_rules()
    except Exception as exc:  # 规则读取失败按无规则处理，不阻塞导入
        logger.warning("学习规则加载失败（本次导入按无规则处理）：%s", exc)
        return 0
    if not rules:
        return 0
    matched = 0
    for rec in records:
        text = str(rec.get("merchant") or "").lower()
        if not text:
            continue
        for rule in rules:
            if rule["pattern"].lower() in text:
                rec["category"] = rule["category"]
                matched += 1
                break
    if matched:
        logger.info("已学习规则归类 %s 条（优先于关键词与 AI）", matched)
    return matched


# ---- 规则管理（设置页规则列表）----


def list_rules() -> list[dict]:
    """规则列表（附 active 计算字段：enabled 且证据达标）"""
    return [
        {**rule, "active": rule["enabled"] and rule["hits"] >= CONFIRM_THRESHOLD}
        for rule in LearnedRuleDAO.list_all()
    ]


def update_rule(rule_id: int, category: str | None, enabled: bool | None) -> dict:
    """编辑规则（改目标分类 / 停用启用）；规则不存在抛 NotFoundError"""
    if LearnedRuleDAO.get(rule_id) is None:
        raise NotFoundError("规则不存在")
    fields: dict = {}
    if category is not None:
        category = category.strip()
        if not category:
            raise ValidationError("目标分类不能为空")
        if not CategoryDAO.get_by_name(category):
            CategoryDAO.create(category)
        fields["category"] = category
    if enabled is not None:
        fields["enabled"] = enabled
    updated = LearnedRuleDAO.update_fields(rule_id, fields)
    if updated is None:
        raise NotFoundError("规则不存在")
    return {
        **updated,
        "active": updated["enabled"] and updated["hits"] >= CONFIRM_THRESHOLD,
    }


def delete_rule(rule_id: int) -> bool:
    """删除规则；不存在返回 False"""
    return LearnedRuleDAO.delete(rule_id)
