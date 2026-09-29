"""家庭月度 AI 复盘（AI-10）：只基于聚合值生成，隐私门控前移到上下文组装

清单拍板的强约束（安全）：复盘**必须只基于聚合值**，不得泄露成员明细；
`allow_detail_view` 门控必须**前移**——即在组装 LLM 上下文时就按门控裁剪
（门控关闭时成员级聚合完全不进提示词），而不是靠提示词约束模型「别输出明细」。
**提示词约束不是安全边界**，本模块按实现要求落为代码结构。

数据来源：family_service.summary（逐成员收支为聚合值）+ budget_service.family_overview
（家庭预算执行）。上下文同时回传给前端「发给 AI 的内容」展开区——口径透明，
与 AI 教练同约定。每次生成 1 次 LLM 调用，手动触发不自动推送（成本可控）。
"""

import json
import logging
from datetime import date

from app.core.errors import ErrorCode, ValidationError
from app.file_settings import load_ai_settings
from app.services import ai_service, budget_service, family_service
from app.services.ai_service import AIClientError
from app.utils.period import valid_month

logger = logging.getLogger(__name__)

REVIEW_LLM_TIMEOUT = 60
REVIEW_MAX_TOKENS = 1000
# 上下文裁剪上限：分类与预算条数（防超大家庭语境撑爆提示词）
REVIEW_TOP_CATEGORIES = 8
REVIEW_TOP_BUDGETS = 8


def monthly_review(
    user_id: str,
    month: str,
    today: date | None = None,
) -> dict:
    """生成家庭月度复盘：上下文按门控裁剪后交 LLM，返回复盘文本与所发内容"""
    today = today or date.today()
    if not valid_month(month):
        raise ValidationError("无效的月份格式，应为 YYYY-MM")
    settings = load_ai_settings()
    if not settings.ready:
        raise AIClientError(
            "尚未配置 AI 供应商，请先在设置页填写并启用",
            code=ErrorCode.AI_NOT_CONFIGURED,
        )

    # 两个聚合源各自走既有成员校验（非成员直接 403/404，不在本层重复造轮子）
    agg = family_service.summary(user_id, month)
    budget = budget_service.family_overview(user_id, month)

    allow_detail = bool(agg["family"]["allow_detail_view"])
    context: dict = {
        "month": month,
        "family_name": agg["family"]["name"],
        "member_count": len(agg["members"]),
        "totals": agg["totals"],
        "categories": agg["categories"][:REVIEW_TOP_CATEGORIES],
    }
    if budget["items"]:
        context["budget"] = {
            "total_budget": budget["total_budget"],
            "total_expense": budget["total_expense"],
            "items": [
                {
                    "category": i["category"] or "总预算",
                    "budget": i["budget"],
                    "expense": i["expense"],
                }
                for i in budget["items"][:REVIEW_TOP_BUDGETS]
            ],
        }
    # 门控前移：明细开关关闭时成员级聚合不进上下文（而不是让模型「别说」）
    if allow_detail:
        context["members"] = [
            {
                "nickname": m.get("nickname") or "成员",
                "income": m["income"],
                "expense": m["expense"],
            }
            for m in agg["members"]
        ]

    if agg["totals"]["income"] <= 0 and agg["totals"]["expense"] <= 0:
        raise ValidationError("该月家庭没有任何记账数据，无法生成复盘")

    review = ai_service.chat(
        settings,
        [
            {"role": "system", "content": _system_prompt(allow_detail)},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
        max_tokens=REVIEW_MAX_TOKENS,
        timeout=REVIEW_LLM_TIMEOUT,
    )
    return {
        "month": month,
        "family": {"id": agg["family"]["id"], "name": agg["family"]["name"]},
        "member_detail_included": allow_detail,
        "review": review.strip(),
        "context": context,
        "notes": [
            "复盘只基于聚合值（家庭合计 / 分类占比 / 预算执行"
            + (" / 成员收支合计" if allow_detail else "")
            + "），任何成员的单笔流水都不会离开本机",
            (
                "成员级数据已按家庭隐私设置进入分析；关闭「成员明细可见」后仅发送家庭合计"
                if allow_detail
                else "家庭未开启「成员明细可见」：成员级数据未发送给 AI，复盘基于家庭合计"
            ),
            "AI 输出仅供参考，数字以家庭页汇总为准",
        ],
    }


def _system_prompt(allow_detail: bool) -> str:
    base = (
        "你是家庭财务复盘助手。用户给出家庭某月的聚合财务数据（合计、分类占比、"
        "预算执行），请写一份 300~500 字的中文月度复盘：\n"
        "1. 总体收支与结余情况；\n"
        "2. 支出结构特点（引用给定的分类数据，占比高的分类点出）；\n"
        "3. 预算执行情况（有预算数据时：哪些超了/省了，没有则不提）；\n"
        "4. 给 1~3 条下月可执行的建议。\n"
        "要求：只使用给定的数字，不得编造或推算未给出的数据；"
        "语气平和友善，不指责任何家庭成员；分点表述，纯文本。"
    )
    if allow_detail:
        base += "\n数据中包含各成员的收支合计（聚合值），可点出成员间的大致分工，但不下评判。"
    else:
        base += "\n数据中不包含任何成员级信息（家庭隐私设置关闭），不要提及或猜测成员个人情况。"
    return base
