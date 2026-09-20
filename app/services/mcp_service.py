"""MCP Server 工具集（T-1.1）：对外暴露只读查询工具（查流水 / 查汇总 / 查预算）

设计要点：
- 零新依赖：MCP Streamable HTTP 以 JSON-RPC 2.0 实现（api/mcp.py），不引入
  mcp SDK——NAS 安装路径对新增依赖最敏感，工具型服务手写协议成本可控
- 只读：全部工具为查询（user_id 由身份链强制注入）；「写操作需显式开关」
  的约束预留为 config.MCP_WRITE_ENABLED（当前无写工具，开关默认关闭）
- 复用既有 DAO / 服务：与开放 API 同一份查询口径，不做第二套实现
"""

from app.core.constants import LOAN_DIRECTION_LABELS, LOAN_STATUSES
from app.db.dao.bill_dao import BillDAO, SORTABLE_FIELDS
from app.db.dao.savings_dao import SavingsGoalDAO
from app.db.dao.stat_dao import StatDAO
from app.services import budget_service
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
