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
from app.services import (
    ai_service,
    budget_service,
    forecast_service,
    loan_service,
    reimb_service,
    savings_service,
    stat_service,
)
from app.utils.amount import round2
from app.utils.period import last_full_months, month_range

logger = logging.getLogger(__name__)

# 上下文窗口：近 N 个完整自然月（与财务健康评分同口径）
_COACH_WINDOW_MONTHS = 6
# 教练回答的 token 上限（回答建议类文本 1024 足够）
_COACH_MAX_TOKENS = 1024

_SYSTEM_PROMPT = (
    "你是一位温和实用的个人财务教练。用户会提供一份结构化的财务摘要"
    "（月均收支、储蓄率、分类占比、预算进度、健康分项、储蓄目标，"
    "视问题可能附有健康评分、支出结构、现金流预测、借贷台账、报销进度），"
    "以及一个问题。请根据数据给出具体可操作的建议。\n"
    "规则：\n"
    "1. 只基于提供的摘要数据回答，不要编造数字；\n"
    "2. 不要索要或猜测任何单笔交易明细；\n"
    "3. 建议要具体（如「餐饮分类月均 X 元，占支出 Y%，可尝试…」），"
    "不要空泛；\n"
    "4. 回答控制在 300 字以内，用中文；\n"
    "5. 如果数据不足以回答（如窗口内无收入记录），如实说明。"
)

# 按问题意图裁剪上下文（脑洞清单 AI-3：上下文变长会稀释关键信息，
# 按需喂入而非无脑全塞）——命中关键词才带上对应板块
_INTENT_KEYWORDS = {
    "loans": ("借", "贷", "欠", "负债", "应收", "应付", "还钱"),
    "reimb": ("报销", "垫付", "报销款", "到账"),
    "forecast": ("预测", "现金流", "未来", "能存", "存款", "结余", "年底", "趋势"),
    "structure": ("固定", "弹性", "必选", "可砍", "订阅"),
    "health": ("健康", "评分", "应急金", "负债率"),
}


def _intent_wants(question: str, section: str) -> bool:
    return any(k in question for k in _INTENT_KEYWORDS[section])


def _build_context(user_id: str, today: date, question: str = "") -> str:
    """从本地 DAO 收集结构化聚合摘要（不包含任何单笔流水）

    基础板块（收支/储蓄率/分类/预算/目标）恒定携带；扩展板块（健康评分/
    支出结构/现金流预测/借贷/报销）按问题关键词裁剪——这些数据都已算好，
    只差接入（AI-3），但全塞会稀释关键信息。
    """
    question = question or ""
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

    # ---- 扩展板块（AI-3）：已算好但此前未喂入的数据，按问题意图裁剪 ----
    if _intent_wants(question, "health"):
        try:
            health = stat_service.health_score(user_id, today=today)
            if health.get("score") is not None:
                items = "；".join(
                    f"{i['label']} {i['score']} 分"
                    for i in health.get("items", [])
                    if i.get("score") is not None
                )
                parts.append(
                    f"财务健康评分：{health['score']}（{health.get('grade', '')}）"
                    + (f"；分项：{items}" if items else "")
                )
            else:
                parts.append("财务健康评分：数据不足，未评分")
        except Exception:
            parts.append("财务健康评分：读取失败")

    if _intent_wants(question, "structure"):
        try:
            structure = forecast_service.expense_structure(user_id, today=today)
            parts.append(
                f"支出结构：固定支出月均 {structure['fixed_monthly']} 元"
                f"（占 {structure['fixed_pct'] if structure['fixed_pct'] is not None else '—'}%），"
                f"弹性支出月均 {structure['flexible_monthly']} 元"
            )
        except Exception:
            parts.append("支出结构：读取失败")

    if _intent_wants(question, "forecast"):
        try:
            cf = forecast_service.cash_flow(user_id, horizon=90, today=today)
            last = cf["points"][-1]
            parts.append(
                f"现金流预测（未来 {cf['horizon_days']} 天，按当前口径外推）："
                f"P50 期末余额 {last['p50']} 元 / P90 悲观 {last['p90']} 元"
                f"（可变支出 P50 月均 {cf['variable']['p50_monthly']}、"
                f"P90 {cf['variable']['p90_monthly']} 元）"
            )
        except Exception:
            parts.append("现金流预测：读取失败")

    if _intent_wants(question, "loans"):
        try:
            loans = loan_service.list_loans(user_id)
            if loans["items"]:
                parts.append(
                    f"借贷台账：未结应收 {loans['receivable']} 元，"
                    f"未结应付 {loans['payable']} 元（共 {len(loans['items'])} 笔）"
                )
            else:
                parts.append("借贷台账：无未结项")
        except Exception:
            parts.append("借贷台账：读取失败")

    if _intent_wants(question, "reimb"):
        try:
            claims = [
                c
                for c in reimb_service.list_claims(user_id)
                if c["status"] in ("pending", "submitted", "partial")
            ]
            if claims:
                unpaid = round2(
                    sum(
                        float(c.get("total_amount") or 0)
                        - float(c.get("received_amount") or 0)
                        for c in claims
                    )
                )
                parts.append(f"报销进度：未结 {len(claims)} 单，未到账合计 {unpaid} 元")
            else:
                parts.append("报销进度：无未结报销单")
        except Exception:
            parts.append("报销进度：读取失败")

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

    context = _build_context(user_id, today, question=text)

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
