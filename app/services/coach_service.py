"""AI 财务教练（T-1.6）：基于结构化摘要的对话式财务建议

隐私红线（计划原文「长期记忆只存本地结构化摘要，不外传原始流水」的落地）：
- 上下文只包含**结构化聚合摘要**（月均收支 / 分类占比 / 预算进度 / 健康
  分项 / 储蓄目标），全部由本地 DAO 计算生成；
- **原始流水永不外传**——不将任何单笔交易记录放进 DeepSeek 请求体；
- 长期记忆即本地数据库本身（bills / budgets / asset_snapshots）——每次
  对话实时从本地重算上下文，无需持久化对话历史或摘要缓存；
- 对话历史由前端传入（≤3 轮），服务端不存储。

本服务不新建任何表、不改任何 schema（v14 即最终形态）。
"""

import logging
from datetime import date


from app.db.dao.stat_dao import StatDAO
from app.core.errors import ValidationError
from app.services.ai_service import AIClientError
from app.file_settings import AISettings
from app.services import ai_service, budget_service, savings_service
from app.utils.amount import round2
from app.utils.period import last_full_months, month_range

logger = logging.getLogger(__name__)

# 上下文窗口：近 N 个完整自然月（与财务健康评分同口径）
_COACH_WINDOW_MONTHS = 6
# 教练回答的 token 上限（回答建议类文本 1024 足够）
_COACH_MAX_TOKENS = 1024

_SYSTEM_PROMPT = (
    "你是一位温和实用的个人财务教练。用户会提供一份结构化的财务摘要"
    "（月均收支、储蓄率、分类占比、预算进度、健康分项、储蓄目标），"
    "以及一个问题。请根据数据给出具体可操作的建议。\n"
    "规则：\n"
    "1. 只基于提供的摘要数据回答，不要编造数字；\n"
    "2. 不要索要或猜测任何单笔交易明细；\n"
    "3. 建议要具体（如「餐饮分类月均 X 元，占支出 Y%，可尝试…」），"
    "不要空泛；\n"
    "4. 回答控制在 300 字以内，用中文；\n"
    "5. 如果数据不足以回答（如窗口内无收入记录），如实说明。"
)


def _build_context(user_id: str, today: date) -> str:
    """从本地 DAO 收集结构化聚合摘要（不包含任何单笔流水）"""
    months = last_full_months(today, _COACH_WINDOW_MONTHS)
    start, end = month_range(months[0])[0], month_range(months[-1])[1]

    trend = StatDAO.month_trend(user_id, start=start, end=end)
    by_month = {row["month"]: row for row in trend}
    incomes = [round2(by_month.get(m, {}).get("income", 0)) for m in months]
    expenses = [round2(by_month.get(m, {}).get("expense", 0)) for m in months]
    avg_income = round2(sum(incomes) / len(months)) if months else 0
    avg_expense = round2(sum(expenses) / len(months)) if months else 0

    parts = [f"评估窗口：{months[0]} ~ {months[-1]}（近 {len(months)} 个完整月）"]
    parts.append(f"月均收入：{avg_income} 元；月均支出：{avg_expense} 元")
    if avg_income > 0:
        rate = round2((avg_income - avg_expense) / avg_income * 100)
        parts.append(f"月均储蓄率：{rate}%")
    else:
        parts.append("月均储蓄率：无收入记录，无法计算")

    # 分类占比（窗口内支出 TOP 5）
    pie = StatDAO.category_pie(user_id, start=start, end=end)[:5]
    total_expense = round2(sum(expenses))
    if pie:
        cats = "；".join(
            f"{r['name'] or '（未分类）'} {round2(float(r['value']))} 元" for r in pie
        )
        parts.append(f"分类支出 TOP5（合计 {total_expense} 元）：{cats}")

    # 预算进度（当月）
    cur_month = f"{today.year:04d}-{today.month:02d}"
    try:
        budget = budget_service.overview(user_id, cur_month)
        if budget["items"]:
            bl = "；".join(
                f"{i['category'] or '总预算'} 已用 {i['expense']}/{i['budget']}"
                for i in budget["items"][:5]
            )
            parts.append(f"当月（{cur_month}）预算：{bl}")
        else:
            parts.append(f"当月（{cur_month}）未设置预算")
    except Exception:
        parts.append("当月预算：读取失败")

    # 储蓄目标
    try:
        goals = savings_service.list_goals(user_id, today=today)
        if goals["items"]:
            gl = "；".join(
                f"{g['name']} 已攒 {g['saved']}/{g['target_amount']}"
                + ("（已达成）" if g["done"] else "")
                for g in goals["items"][:3]
            )
            parts.append(f"储蓄目标：{gl}")
        else:
            parts.append("储蓄目标：未设置")
    except Exception:
        parts.append("储蓄目标：读取失败")

    return "\n".join(parts)


def coach_chat(
    user_id: str,
    question: str,
    history: list[dict] | None = None,
    today: date | None = None,
    settings: AISettings | None = None,
) -> dict:
    """AI 财务教练对话入口：结构化摘要上下文 + DeepSeek 建议回答

    返回 {question, context, answer}；未配置 API Key 或调用失败抛
    AIClientError（前端 toast 提示，不影响其他功能）。
    """
    today = today or date.today()
    text = " ".join(str(question or "").split())
    if not text:
        raise ValidationError("问题不能为空")
    if len(text) > 500:
        raise ValidationError("问题过长，请精简后重试（500 字以内）")

    settings = settings or ai_service.load_ai_settings()
    if not settings.ready:
        raise AIClientError("尚未配置 DeepSeek API Key，请先在设置页填写")

    context = _build_context(user_id, today)

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    # 追问上下文（≤3 轮）：只带问答文本，不重复摘要
    for h in (history or [])[-3:]:
        messages.append({"role": "user", "content": h.get("question", "")})
        messages.append({"role": "assistant", "content": h.get("answer", "")})
    messages.append(
        {
            "role": "user",
            "content": f"【我的财务摘要】\n{context}\n\n【我的问题】{text}",
        }
    )

    answer = ai_service.chat(settings, messages, max_tokens=_COACH_MAX_TOKENS)
    logger.info("AI 教练对话（账号 %s，问题 %u 字）：%s", user_id, len(text), text[:60])
    return {"question": text, "context": context, "answer": answer.strip()}
