"""场景化预算模板建议（AI-6）：LLM 只做「选」不做「算」

清单拍板的设计：现有预算建议是纯统计中位数（forecast_service.budget_suggestions），
本模块补第二种可选口径——把结构化摘要交给 LLM，让它从**预置模板库**里选一个
最贴合的生活场景（学生党/通勤族/…），并把用户的消费分类映射到模板槽位；
模板本身是**硬编码的收入占比系数表**，数字全部由后端按「收入月均 × 系数」计算，
LLM 不产出任何金额（防幻觉）。

定位为统计建议的**补充而非替代**：未被映射到槽位的分类回落统计中位数，响应里
逐条标明来源（template / statistic），前端并排展示、采纳走同一入口。

安全与成本：只外传聚合值（收入月均 + 分类月均中位数，不传任何单笔流水）；
每次推荐恰 1 次 LLM 调用、max_tokens 钳制；T-1.7 隐私加密已落地（api_key
密文存储），满足清单 §4.1「加密先于 AI 扩展」的前置约束。
"""

import json
import logging
import statistics
from datetime import date
from typing import Optional

from app.core.errors import ErrorCode, ValidationError
from app.db.dao.stat_dao import StatDAO
from app.file_settings import load_ai_settings
from app.services import ai_service, forecast_service
from app.services.ai_service import AIClientError
from app.utils.amount import round2
from app.utils.text import strip_code_fence

logger = logging.getLogger(__name__)

# 模板槽位 = 通用财务维度（LLM 把用户分类映射到槽位上）；系数 = 占月收入比
# 各模板系数合计刻意 < 1：给储蓄/机动留白，模板不做「月光」规划
TEMPLATES: dict[str, dict] = {
    "student": {
        "label": "学生党",
        "description": "在校或刚毕业，收入有限，餐饮与学习为主，娱乐留少量弹性",
        "factors": {
            "餐饮": 0.30,
            "住房": 0.15,
            "学习": 0.10,
            "购物": 0.10,
            "娱乐": 0.08,
            "交通": 0.06,
            "通讯": 0.03,
            "医疗": 0.02,
        },
    },
    "commuter": {
        "label": "通勤族",
        "description": "上班族，通勤与外食占比高，收入相对稳定",
        "factors": {
            "住房": 0.25,
            "餐饮": 0.22,
            "交通": 0.10,
            "购物": 0.12,
            "人情": 0.05,
            "娱乐": 0.08,
            "学习": 0.04,
            "通讯": 0.03,
            "医疗": 0.03,
        },
    },
    "family3": {
        "label": "三口之家",
        "description": "有孩子同住，食材采买与育儿支出为主，医疗留存不可省",
        "factors": {
            "住房": 0.25,
            "餐饮": 0.25,
            "育儿": 0.12,
            "购物": 0.12,
            "交通": 0.08,
            "医疗": 0.05,
            "人情": 0.05,
            "娱乐": 0.04,
            "学习": 0.03,
        },
    },
    "mortgage": {
        "label": "还贷期",
        "description": "月供压力大，餐饮购物刻意收紧，娱乐最低配置",
        "factors": {
            "住房": 0.35,
            "餐饮": 0.18,
            "交通": 0.08,
            "购物": 0.08,
            "通讯": 0.03,
            "医疗": 0.03,
            "人情": 0.04,
            "娱乐": 0.04,
        },
    },
    "homebody": {
        "label": "居家生活",
        "description": "在家做饭为主，网购与订阅服务是主要弹性支出",
        "factors": {
            "住房": 0.22,
            "餐饮": 0.25,
            "购物": 0.12,
            "娱乐": 0.08,
            "交通": 0.05,
            "通讯": 0.03,
            "医疗": 0.03,
            "人情": 0.05,
        },
    },
    "freelancer": {
        "label": "收入不稳定",
        "description": "自由职业/项目制收入波动大，系数整体压低留足缓冲",
        "factors": {
            "住房": 0.20,
            "餐饮": 0.20,
            "交通": 0.06,
            "购物": 0.08,
            "学习": 0.05,
            "医疗": 0.05,
            "娱乐": 0.06,
            "通讯": 0.03,
        },
    },
}

TEMPLATE_LLM_TIMEOUT = 30
TEMPLATE_MAX_TOKENS = 400
# 槽位系数上限（防御值）：系数表是硬编码的，这里只为抵御未来改表的笔误
_TEMPLATE_FACTOR_MAX = 0.6


def template_suggestion(
    user_id: str,
    month: Optional[str] = None,
    today: date | None = None,
    ledger_id: Optional[int] = None,
) -> dict:
    """场景模板预算建议：LLM 选模板 + 分类映射，金额按「收入月均 × 系数」计算

    未被映射到槽位的分类回落统计中位数（复用 forecast_service.budget_suggestions，
    含一次性大额剔除口径），响应逐条标明 source。无收入记录时显式报错——
    收入占比模板在无收入口径下没有意义，不做「拿支出当分母」的静默降级。
    """
    today = today or date.today()
    settings = load_ai_settings()
    if not settings.ready:
        raise AIClientError(
            "尚未配置 AI 供应商，请先在设置页填写并启用",
            code=ErrorCode.AI_NOT_CONFIGURED,
        )

    # 统计建议复用既有口径（窗口/剔除/中位数），同时充当「未映射分类」的回落值
    stat = forecast_service.budget_suggestions(
        user_id, month, today=today, ledger_id=ledger_id
    )
    stat_by_cat = {s["category"]: s for s in stat["suggestions"]}
    if not stat_by_cat:
        raise ValidationError(
            "近 6 个月没有出现 ≥ 3 个月的分类支出，无法生成模板建议",
            code=ErrorCode.BUDGET_INVALID,
        )

    income_monthly = _income_monthly(user_id, stat["window"], ledger_id)
    if income_monthly <= 0:
        raise ValidationError(
            "近 6 个月没有收入记录，无法按收入占比生成模板建议",
            code=ErrorCode.BUDGET_INVALID,
        )

    template, mapping = _select_template(settings, income_monthly, stat["suggestions"])
    factors = template["factors"]

    # 同槽位多分类：槽位金额按各分类统计中位数占比分摊（买菜/外卖同归餐饮的场景）
    by_slot: dict[str, list[str]] = {}
    for category, slot in mapping.items():
        by_slot.setdefault(slot, []).append(category)

    items = []
    for s in stat["suggestions"]:
        category = s["category"]
        slot = mapping.get(category)
        if slot is None or slot not in factors:
            items.append(_stat_item(s))
            continue
        peers = by_slot[slot]
        slot_amount = income_monthly * factors[slot]
        if len(peers) == 1:
            suggested = slot_amount
        else:
            total = sum(stat_by_cat[c]["median"] for c in peers) or len(peers)
            suggested = (
                slot_amount * (s["median"] / total)
                if total > 0
                else slot_amount / len(peers)
            )
        items.append(
            {
                "category": category,
                "suggested": round2(suggested),
                "low": round2(suggested * forecast_service.SUGGEST_LOW_FACTOR),
                "high": round2(suggested * forecast_service.SUGGEST_HIGH_FACTOR),
                "slot": slot,
                "source": "template",
                "statistical_suggested": s["suggested"],
                "current_budget": s["current_budget"],
            }
        )

    return {
        "month": stat["month"],
        "window": stat["window"],
        "income_monthly": income_monthly,
        "template": {
            "id": template["_id"],
            "label": template["label"],
            "description": template["description"],
            "factors": factors,
        },
        "mapping": mapping,
        "items": items,
        "notes": [
            "模板系数为硬编码的占月收入比，金额 = 收入月均 × 系数，LLM 不产出金额",
            "未被映射到模板槽位的分类回落统计中位数（与「智能建议」同口径）",
            "建议区间为建议值 × 0.9 ~ × 1.1，采纳走既有预算 upsert，可编辑微调",
        ],
    }


def _stat_item(s: dict) -> dict:
    """未映射分类的回落条目：保留统计建议值，标明来源与被跳过的原因由前端统一渲染"""
    return {
        "category": s["category"],
        "suggested": s["suggested"],
        "low": s["low"],
        "high": s["high"],
        "slot": None,
        "source": "statistic",
        "statistical_suggested": s["suggested"],
        "current_budget": s["current_budget"],
    }


def _income_monthly(user_id: str, window: dict, ledger_id: Optional[int]) -> float:
    """收入月均口径：窗口内非零月收入合计的中位数（与订阅侦探同口径）"""
    by_month: dict[str, float] = {}
    for r in StatDAO.forecast_rows(
        user_id,
        start=window["start"],
        end=window["end"],
        tx_type="income",
        ledger_id=ledger_id,
    ):
        month = str(r["tx_time"] or "")[:7]
        if len(month) == 7:
            by_month[month] = by_month.get(month, 0.0) + float(r["amount"] or 0)
    nonzero = [v for v in by_month.values() if v > 0]
    return round2(statistics.median(nonzero)) if nonzero else 0.0


def _select_template(
    settings, income_monthly: float, suggestions: list[dict]
) -> tuple[dict, dict[str, str]]:
    """单次 LLM 调用：选模板 id + 把用户分类映射到槽位；输出不合法显式报错"""
    candidates = [
        {
            "id": t["_id"],
            "label": t["label"],
            "description": t["description"],
            "slots": list(t["factors"]),
        }
        for t in _iter_templates()
    ]
    user_categories = [
        {"category": s["category"], "monthly_median": s["median"]} for s in suggestions
    ]
    system = (
        "你是预算模板选择助手。根据用户的月均收入与支出分类，做两件事：\n"
        "1. 从候选模板中选一个最贴合用户生活状态的（template 只能是候选 id 之一）；\n"
        "2. 把用户的每个分类映射到该模板的槽位（mapping），一个分类一个槽位；"
        "没有合适槽位的分类不要强行映射（直接不出现在 mapping 里）。\n"
        '输出 JSON：{"template": "<id>", "mapping": {"<用户分类>": "<槽位>"}}，不要输出其他内容。'
    )
    user = json.dumps(
        {
            "income_monthly": income_monthly,
            "categories": user_categories,
            "templates": candidates,
        },
        ensure_ascii=False,
    )
    content = ai_service.chat(
        settings,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=TEMPLATE_MAX_TOKENS,
        timeout=TEMPLATE_LLM_TIMEOUT,
    )
    try:
        data = json.loads(strip_code_fence(content))
    except (ValueError, TypeError) as exc:
        raise AIClientError(f"模板选择结果解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise AIClientError("模板选择结果必须是 JSON 对象")
    template_id = str(data.get("template") or "").strip()
    template = next((t for t in _iter_templates() if t["_id"] == template_id), None)
    if template is None:
        raise AIClientError(f"AI 返回了未知模板：{template_id}")
    raw_mapping = data.get("mapping")
    if not isinstance(raw_mapping, dict):
        raise AIClientError("模板映射缺失（mapping 必须是对象）")

    known = {s["category"] for s in suggestions}
    mapping: dict[str, str] = {}
    for category, slot in raw_mapping.items():
        category = str(category).strip()[:64]
        slot = str(slot).strip()[:32]
        if category in known and slot in template["factors"]:
            mapping[category] = slot
    unknown_slots = {
        str(v)
        for v in raw_mapping.values()
        if str(v).strip() not in template["factors"]
    }
    if unknown_slots:
        logger.info("模板映射包含未知槽位，已忽略：%s", sorted(unknown_slots))
    return template, mapping


def _iter_templates():
    for tid, t in TEMPLATES.items():
        factors = {k: float(v) for k, v in t["factors"].items()}
        bad = {k: v for k, v in factors.items() if not 0 < v <= _TEMPLATE_FACTOR_MAX}
        if bad:
            raise RuntimeError(f"模板系数表配置越界，请修正：{tid} {bad}")
        yield {
            "_id": tid,
            "label": t["label"],
            "description": t["description"],
            "factors": factors,
        }
