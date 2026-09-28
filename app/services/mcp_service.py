"""MCP Server 工具集（T-1.1 + AI-7 扩展 + AI-8）：对外暴露只读查询工具

当前 9 个工具覆盖「收支 + 预测 + 健康 + 借贷 + 备注检索」：
- 查流水 query_bills / 查汇总 query_summary / 查预算 query_budget /
  查储蓄目标 query_savings_goals（T-1.1）
- 查现金流预测 query_forecast / 查健康评分 query_health /
  查支出结构 query_expense_structure / 查借贷台账 query_loans（AI-7）
- 备注语义检索 query_note_search（AI-8，零依赖 TF-IDF 模糊匹配）

设计要点：
- 零新依赖：MCP Streamable HTTP 以 JSON-RPC 2.0 实现（api/mcp.py），不引入
  mcp SDK——NAS 安装路径对新增依赖最敏感，工具型服务手写协议成本可控
- 只读：全部工具为查询（user_id 由身份链强制注入）；「写操作需显式开关」
  的约束预留为 config.MCP_WRITE_ENABLED（当前无写工具，开关默认关闭）
- 复用既有 DAO / 服务：与开放 API 同一份查询口径，不做第二套实现；
  文本输出为面向外部 Agent 的摘要（明细走 query_bills 翻页）
"""

from app.db.dao.bill_dao import BillDAO
from app.db.dao.savings_dao import SavingsGoalDAO
from app.db.dao.stat_dao import StatDAO
from app.services import (
    budget_service,
    forecast_service,
    loan_service,
    note_search_service,
    stat_service,
)
from app.utils.amount import round2

SERVER_INFO = {"name": "fn-finstat", "title": "财务统计 MCP"}

_TOOLS: dict[str, dict] = {}


def tool(name: str, description: str, input_schema: dict):
    """工具注册装饰器：handler 签名统一 (user_id, arguments) -> str（文本结果）"""

    def wrap(fn):
        _TOOLS[name] = {
            "name": name,
            "description": description,
            "input_schema": input_schema,
            "handler": fn,
        }
        return fn

    return wrap


def list_tools() -> list[dict]:
    """tools/list 结果（MCP inputSchema 格式）"""
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "inputSchema": {
                "type": "object",
                "properties": t["input_schema"],
                "required": [
                    k for k, v in t["input_schema"].items() if v.get("required")
                ],
            },
        }
        for t in _TOOLS.values()
    ]


def call_tool(name: str, arguments: dict, user_id: str) -> str:
    """执行工具调用；未知工具 / 参数不合法抛 ValueError（转 MCP isError 结果）"""
    t = _TOOLS.get(name)
    if t is None:
        raise ValueError(f"未知工具：{name}")
    return t["handler"](user_id, arguments or {})


def _opt_str(args: dict, key: str) -> str | None:
    value = args.get(key)
    return str(value).strip() if value not in (None, "") else None


def _opt_int(args: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(args.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(lo, min(value, hi))


@tool(
    "query_bills",
    "查询流水明细：按时间区间、账户、收支类型、分类、商户关键词筛选，按时间倒序分页返回",
    {
        "start": {"type": "string", "description": "起始日期 YYYY-MM-DD（可选）"},
        "end": {"type": "string", "description": "结束日期 YYYY-MM-DD，含当日（可选）"},
        "account": {
            "type": "string",
            "description": "账户类型 wechat/alipay/jd/unionpay（可选）",
        },
        "tx_type": {
            "type": "string",
            "description": "收支类型 expense/income/transfer（可选）",
        },
        "category": {"type": "string", "description": "消费分类精确匹配（可选）"},
        "merchant": {"type": "string", "description": "商户子串模糊匹配（可选）"},
        "page": {"type": "integer", "description": "页码，默认 1"},
        "page_size": {"type": "integer", "description": "每页条数 1-100，默认 20"},
    },
)
def _tool_query_bills(user_id: str, args: dict) -> str:
    merchant = _opt_str(args, "merchant")
    total, rows = BillDAO.list_bills(
        user_id,
        start=_opt_str(args, "start"),
        end=_opt_str(args, "end"),
        account=_opt_str(args, "account"),
        tx_type=_opt_str(args, "tx_type"),
        category=_opt_str(args, "category"),
        merchants=[merchant] if merchant else None,
        page=_opt_int(args, "page", 1, 1, 10000),
        page_size=_opt_int(args, "page_size", 20, 1, 100),
    )
    lines = [f"共 {total} 条，本页 {len(rows)} 条（按时间倒序）："]
    for b in rows:
        sign = (
            "-"
            if b["tx_type"] == "expense"
            else ("+" if b["tx_type"] == "income" else "±")
        )
        lines.append(
            f"#{b['id']} {b['tx_time'][:10]} [{b['tx_type']}] {b['merchant'] or '（无商户）'} "
            f"{sign}{b['amount']} 元（{b['category']}）"
        )
    return "\n".join(lines)


@tool(
    "query_summary",
    "查询收支汇总：指定时间区间的总收入 / 总支出 / 结余，以及分类支出占比 TOP",
    {
        "start": {"type": "string", "description": "起始日期 YYYY-MM-DD（可选）"},
        "end": {"type": "string", "description": "结束日期 YYYY-MM-DD，含当日（可选）"},
        "top_categories": {
            "type": "integer",
            "description": "分类占比返回条数 1-20，默认 10",
        },
    },
)
def _tool_query_summary(user_id: str, args: dict) -> str:
    start, end = _opt_str(args, "start"), _opt_str(args, "end")
    top_n = _opt_int(args, "top_categories", 10, 1, 20)
    summary = StatDAO.summary(user_id, start=start, end=end)
    income, expense = round2(summary["income"]), round2(summary["expense"])
    lines = [
        f"区间 {start or '不限'} ~ {end or '不限'}：收入 {income} 元，支出 {expense} 元，结余 {round2(income - expense)} 元",
    ]
    if expense > 0:
        pie = StatDAO.category_pie(user_id, start=start, end=end)[:top_n]
        lines.append("分类支出 TOP：")
        for row in pie:
            pct = round2(float(row["value"]) / expense * 100)
            lines.append(
                f"- {row['name'] or '（未分类）'}：{round2(float(row['value']))} 元（{pct}%）"
            )
    return "\n".join(lines)


@tool(
    "query_budget",
    "查询某月预算进度：逐条预算对比当月实际支出（含家庭预算）",
    {
        "month": {
            "type": "string",
            "description": "月份 YYYY-MM，如 2026-09（可选，默认当月）",
        },
    },
)
def _tool_query_budget(user_id: str, args: dict) -> str:
    from datetime import date

    month = _opt_str(args, "month")
    if not month:
        today = date.today()
        month = f"{today.year:04d}-{today.month:02d}"
    overview = budget_service.overview(user_id, month)
    lines = [
        f"{month} 预算总览：预算 {overview['total_budget']} 元，"
        f"已支出 {overview['total_expense']} 元"
    ]
    for item in overview["items"]:
        label = item["category"] or "总预算"
        lines.append(
            f"- {label}：预算 {item['budget']} 元，已用 {item['expense']} 元，"
            f"剩余 {item['remaining']} 元"
        )
    if not overview["items"]:
        lines.append("（当月未设置任何预算）")
    return "\n".join(lines)


@tool(
    "query_savings_goals",
    "查询储蓄目标列表与进度（进度 = 创建日以来累计净结余，由流水自动计算）",
    {},
)
def _tool_query_savings(user_id: str, args: dict) -> str:
    from datetime import date

    goals = SavingsGoalDAO.list_goals(user_id)
    if not goals:
        return "暂无储蓄目标"
    today = date.today()
    lines = []
    for g in goals:
        summary = StatDAO.summary(user_id, start=g["start_date"])
        saved = round2(summary["income"] - summary["expense"])
        state = (
            "已达成 ✅"
            if saved >= g["target_amount"]
            else f"还差 {round2(max(g['target_amount'] - saved, 0))} 元"
        )
        lines.append(
            f"- {g['name']}：目标 {round2(g['target_amount'])} 元，已攒 {saved} 元（{state}）"
            + (f"，目标日 {g['target_date']}" if g["target_date"] else "")
            + (
                f"，{round2(max((date.fromisoformat(g['target_date']) - today).days, 0))} 天"
                if g["target_date"]
                else ""
            )
        )
    return "\n".join(lines)


@tool(
    "query_forecast",
    "查询现金流预测：起点余额、固定收支项、可变支出 P50/P90 月度水平与预测期末余额（预期 P50 / 悲观 P90）",
    {
        "horizon": {"type": "integer", "description": "预测天数 7-180，默认 90"},
    },
)
def _tool_query_forecast(user_id: str, args: dict) -> str:
    cf = forecast_service.cash_flow(
        user_id, horizon=_opt_int(args, "horizon", 90, 7, 180)
    )
    lines = [
        f"现金流预测（未来 {cf['horizon_days']} 天）：起点余额 {cf['start_balance']} 元"
        f"（来源：{'资产快照' if cf['start_source'] == 'asset_snapshot' else '全部流水净额'}）",
        f"可变支出月度水平：P50 {cf['variable']['p50_monthly']} 元，P90 {cf['variable']['p90_monthly']} 元",
    ]
    for item in cf["fixed_items"]:
        kind = "收入" if item["tx_type"] == "income" else "支出"
        lines.append(
            f"- 固定{kind}：{item['merchant']} 每月 {item['monthly_amount']} 元"
            f"（{item['day_of_month']} 号）"
        )
    if not cf["fixed_items"]:
        lines.append("- 未识别到固定收支项（近 3 个完整月无每月稳定出现的同商户收支）")
    if cf["excluded_items"]:
        lines.append(
            f"- 另有 {len(cf['excluded_items'])} 项固定项在预测口径中被排除，已计入可变支出"
        )
    last = cf["points"][-1] if cf["points"] else None
    if last:
        lines.append(
            f"期末余额（{last['date']}）：预期 P50 {last['p50']} 元，悲观 P90 {last['p90']} 元"
        )
    return "\n".join(lines)


@tool(
    "query_health",
    "查询财务健康评分：总分与等级，储蓄率 / 负债率 / 应急金月数三分项（缺数据的分项如实说明）",
    {},
)
def _tool_query_health(user_id: str, args: dict) -> str:
    hs = stat_service.health_score(user_id)
    score = "暂无法评估" if hs["score"] is None else f"{hs['score']} 分"
    lines = [
        f"财务健康评分：{score}（{hs['grade']}），评估窗口 {hs['window']['start']} ~ {hs['window']['end']}"
    ]
    for it in hs["items"]:
        if it["available"]:
            # 储蓄率/负债率的 value 是百分数（服务层 unit 为空串，界面自行补 %），
            # 纯文本输出补上 % 避免外部 Agent 误读为小数
            unit = it["unit"] or ("%" if it["key"] in ("savings", "debt") else "")
            lines.append(
                f"- {it['label']}：{it['value']}{unit}，得分 {round2(it['score'])}"
            )
        else:
            lines.append(f"- {it['label']}：暂无法评估（{it['hint']}）")
    return "\n".join(lines)


@tool(
    "query_expense_structure",
    "查询支出结构拆分：近 6 个完整月的固定支出（必选项）与弹性支出（可砍项）月均水平",
    {},
)
def _tool_query_expense_structure(user_id: str, args: dict) -> str:
    es = forecast_service.expense_structure(user_id)
    fixed_pct = "—" if es["fixed_pct"] is None else f"{es['fixed_pct']}%"
    lines = [
        f"支出结构（{es['window']['start']} ~ {es['window']['end']}，月均合计 "
        f"{es['total_monthly']} 元）：必选项 {es['fixed_monthly']} 元/月（占 {fixed_pct}），"
        f"可砍项 {es['flexible_monthly']} 元/月"
    ]
    for f in es["fixed"][:8]:
        lines.append(f"- 必选：{f['merchant']} 每月 {f['monthly_amount']} 元")
    if len(es["fixed"]) > 8:
        lines.append(f"- （必选项还有 {len(es['fixed']) - 8} 个未列出）")
    if not es["fixed"]:
        lines.append("- 未识别到每月稳定出现的固定支出")
    for f in es["flexible"][:5]:
        lines.append(
            f"- 可砍：{f['merchant']} 月均 {f['monthly_amount']} 元（{f['count']} 笔）"
        )
    if len(es["flexible"]) > 5:
        lines.append(f"- （可砍项还有 {len(es['flexible']) - 5} 个未列出）")
    return "\n".join(lines)


@tool(
    "query_loans",
    "查询借贷台账：应收（借出）/ 应付（借入）未结汇总与逐笔还款进度",
    {},
)
def _tool_query_loans(user_id: str, args: dict) -> str:
    data = loan_service.list_loans(user_id)
    lines = [
        f"借贷台账：应收（借出未结）合计 {data['receivable']} 元，"
        f"应付（借入未结）合计 {data['payable']} 元"
    ]
    if not data["items"]:
        lines.append("（暂无借贷记录）")
    for r in data["items"]:
        state = "已结清" if r["status"] == "settled" else "进行中"
        due = f"，到期 {r['due_date']}" if r["due_date"] else ""
        lines.append(
            f"- #{r['id']} {r['direction_label']} {r['counterparty'] or '（无对方）'}："
            f"本金 {round2(r['principal'])} 元，已还 {round2(r['repaid'])} 元，"
            f"剩余 {r['remaining']} 元（{state}{due}）"
        )
    return "\n".join(lines)


@tool(
    "query_note_search",
    "备注语义检索：用模糊描述（如「给家里人买东西」「看病买药」）按相关度匹配流水的"
    "商户与备注，突破关键词精确匹配（零依赖 TF-IDF）",
    {
        "q": {"type": "string", "description": "检索词，支持模糊语义描述"},
        "top_k": {"type": "integer", "description": "返回条数 1-50，默认 10"},
        "tx_type": {
            "type": "string",
            "description": "收支类型 expense/income/transfer（可选）",
        },
    },
)
def _tool_query_note_search(user_id: str, args: dict) -> str:
    q = _opt_str(args, "q")
    if not q:
        raise ValueError("检索词 q 不能为空")
    data = note_search_service.search_notes(
        user_id,
        q,
        top_k=_opt_int(args, "top_k", 10, 1, 50),
        tx_type=_opt_str(args, "tx_type"),
    )
    if not data["results"]:
        return f"未检索到与「{q}」相关的流水（索引 {data['indexed']} 条）"
    lines = [
        f"与「{q}」语义最相关的 {len(data['results'])} 条（索引 {data['indexed']} 条）："
    ]
    for r in data["results"]:
        sign = (
            "-"
            if r["tx_type"] == "expense"
            else ("+" if r["tx_type"] == "income" else "±")
        )
        lines.append(
            f"#{r['id']} {r['tx_time'][:10]} [{r['tx_type']}] {r['merchant'] or '（无商户）'} "
            f"{sign}{r['amount']} 元 备注：{r['remark'] or '无'}（相关度 {r['score']:.2f}）"
        )
    return "\n".join(lines)
