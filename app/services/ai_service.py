"""DeepSeek 智能分类：调用 DeepSeek Chat API 对流水做语义归类

设计要点：
- 关键词归类（category_matcher）先执行且零成本，AI 只兜底关键词未命中的记录，
  控制调用量与费用
- DeepSeek 接口为 OpenAI 兼容协议，用标准库 urllib 调用，不新增运行时依赖
- AI 返回的分类必须出现在候选分类中才采纳，否则保留原分类（防幻觉编造分类）
- 批量请求按批容错：单批失败只影响当批记录，导入/重分类主流程不受影响
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from datetime import date

from app.config import DEFAULT_CATEGORY, AISettings, load_ai_settings
from app.core.errors import BizError, ErrorCode
from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from app.db.dao.stat_dao import StatDAO
from app.schemas.ai import AITestResult
from app.utils.amount import round2 as _round2
from app.utils.period import month_range, prev_month as _prev_month, valid_month

logger = logging.getLogger(__name__)

# 每次请求打包的流水条数：过大易超时/输出截断，过小则请求次数偏多
BATCH_SIZE = 50
# 单次请求超时（秒）：deepseek-reasoner 出 token 较慢，取宽裕值
REQUEST_TIMEOUT = 60
# 导入时 AI 归类阶段的时间预算（秒）：超时后剩余记录保留关键词结果，避免导入久等
IMPORT_TIME_BUDGET = 90
# 「AI 智能分类」单次最多处理的流水条数（分批执行），超出可再次点击续跑
CLASSIFY_LIMIT = 1000
# 「AI 智能分类」单次时间预算（秒）：同步 HTTP 请求不能无限等，超时后可再次点击续跑
CLASSIFY_TIME_BUDGET = 240

# 批量归类防重入锁：任务未结束时再次触发直接拒绝（非阻塞），避免重复调 API 与相互覆盖写入
_CLASSIFY_LOCK = threading.Lock()


class AIClientError(BizError):
    """DeepSeek 调用失败（网络/鉴权/限流/响应异常等），message 为用户可读信息

    继承 BizError：由全局异常处理器统一转 HTTP 400，路由层无需 try/except 翻译。
    """

    default_code = ErrorCode.AI_CALL_FAILED


_TX_TYPE_LABEL = {"expense": "支出", "income": "收入", "transfer": "转账"}

_SYSTEM_PROMPT = (
    "你是个人记账分类助手。用户会给出若干笔交易（编号、商户、备注、类型、金额）"
    "和候选消费分类，请为每笔交易从候选分类中选出最合适的一个。"
    "只能使用候选分类中的名称，不要发明新分类。"
    '输出 JSON 对象：{"result": {"<编号>": "<分类名>"}}，编号必须与输入一致，'
    "每笔交易都必须给出分类。"
)


def _chat(settings: AISettings, messages: list[dict], max_tokens: int) -> str:
    """调用 chat/completions 返回文本内容；网络/协议错误统一抛 AIClientError"""
    url = settings.base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": settings.model,
        "messages": messages,
        "temperature": 0.1,  # 分类任务稳定优先
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.api_key.strip()}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = str(body.get("error", {}).get("message") or body)
        except Exception:
            detail = str(exc.reason)
        raise AIClientError(f"DeepSeek 接口返回 {exc.code}：{detail}") from exc
    except TimeoutError as exc:
        raise AIClientError(f"DeepSeek 请求超时（>{REQUEST_TIMEOUT}s）") from exc
    except urllib.error.URLError as exc:
        raise AIClientError(f"无法连接 DeepSeek 服务：{exc.reason}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AIClientError("DeepSeek 响应不是有效 JSON") from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIClientError(
            f"DeepSeek 响应格式异常：{json.dumps(data, ensure_ascii=False)[:200]}"
        ) from exc
    if not isinstance(content, str):
        raise AIClientError("DeepSeek 响应缺少消息内容")
    return content


def test_connection(settings: AISettings) -> AITestResult:
    """发起一次极小的对话请求，验证 Key / 网络 / 模型可用性"""
    try:
        _chat(
            settings,
            [{"role": "user", "content": '请只输出 JSON：{"ok": true}'}],
            max_tokens=16,
        )
    except AIClientError as exc:
        return AITestResult(ok=False, message=str(exc))
    return AITestResult(ok=True, message="连接成功")


def _build_user_prompt(records: list[dict], categories: list[str]) -> str:
    lines = ["候选分类：" + "、".join(categories), "", "交易列表："]
    for i, rec in enumerate(records):
        type_label = _TX_TYPE_LABEL.get(rec.get("tx_type"), "支出")
        lines.append(
            f"{i}. 商户：{rec.get('merchant') or '未知'}"
            f" | 备注：{rec.get('remark') or '无'}"
            f" | 类型：{type_label} | 金额：{rec.get('amount')}"
        )
    return "\n".join(lines)


def _parse_assignments(content: str, total: int, allowed: set[str]) -> dict[int, str]:
    """解析 AI 返回的 JSON，仅保留编号合法且分类在候选列表内的结果"""
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # 模型偶发在 JSON 外包裹 ``` 围栏，剥掉后重试一次
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("```", 2)[1]
            if stripped.startswith("json"):
                stripped = stripped[4:]
        try:
            data = json.loads(stripped.strip())
        except json.JSONDecodeError as exc:
            raise AIClientError(f"AI 返回内容不是有效 JSON：{content[:120]}") from exc
    mapping = data.get("result") if isinstance(data, dict) else None
    if not isinstance(mapping, dict):
        raise AIClientError("AI 返回 JSON 缺少 result 字段")
    result: dict[int, str] = {}
    for key, category in mapping.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < total:
            continue
        name = str(category).strip()
        if name in allowed:
            result[idx] = name
    return result


def classify_records(
    records: list[dict], categories: list[str], settings: AISettings
) -> dict[int, str]:
    """把一批流水交给 DeepSeek 归类，返回 {记录下标: 分类}；失败抛 AIClientError"""
    # 去重并保持传入顺序（分类表 id 序），保证 prompt 稳定可读
    ordered: list[str] = []
    for c in categories:
        if c and c not in ordered:
            ordered.append(c)
    if not records or not ordered or not settings.ready:
        return {}
    content = _chat(
        settings,
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(records, ordered)},
        ],
        max_tokens=min(4000, 64 * len(records) + 200),
    )
    return _parse_assignments(content, len(records), set(ordered))


def classify_batches(
    records: list[dict],
    categories: list[str],
    settings: AISettings,
    time_budget: float | None = None,
) -> tuple[dict[int, str], bool]:
    """分批归类（按批容错），time_budget 秒用尽即停

    返回 ({记录下标: 分类}, 是否处理完全部批次)；时间预算用尽时 completed=False。
    """
    assignments: dict[int, str] = {}
    deadline = time.monotonic() + time_budget if time_budget else None
    for start in range(0, len(records), BATCH_SIZE):
        if deadline is not None and time.monotonic() >= deadline:
            logger.warning(
                "AI 归类时间预算用尽，剩余 %s 条保留原分类", len(records) - start
            )
            return assignments, False
        batch = records[start : start + BATCH_SIZE]
        try:
            # classify_records 返回批内下标，合并时平移为全局下标
            batch_result = classify_records(batch, categories, settings)
            assignments.update({start + idx: cat for idx, cat in batch_result.items()})
        except AIClientError as exc:
            logger.warning("AI 归类单批失败（该批保留原分类）：%s", exc)
    return assignments, True


def enhance_import_records(records: list[dict]) -> int:
    """导入时对关键词未命中（"其他"）的记录做 AI 二次归类，返回改写分类的条数

    未启用 / 未配置密钥直接跳过；任何异常都不影响导入主流程。
    原地改写 records 中各记录的 category（仅限候选分类，AI 结果已过白名单校验）。
    """
    settings = load_ai_settings()
    if not settings.ready or not settings.enabled:
        return 0
    pending = [
        i for i, rec in enumerate(records) if rec.get("category") == DEFAULT_CATEGORY
    ]
    if not pending:
        return 0
    categories = [c["name"] for c in CategoryDAO.list_all()]
    try:
        assignments, _completed = classify_batches(
            [records[i] for i in pending],
            categories,
            settings,
            time_budget=IMPORT_TIME_BUDGET,
        )
    except Exception as exc:  # 兜底：分类失败绝不阻断导入
        logger.warning("导入时 AI 归类失败：%s", exc)
        return 0
    logger.info(
        "导入智能归类：%s 条关键词未命中，AI 改写 %s 条", len(pending), len(assignments)
    )
    for local_idx, category in assignments.items():
        records[pending[local_idx]]["category"] = category
    return len(assignments)


def reclassify_bills(user_id: str, scope: str = "unmatched") -> dict:
    """对当前账号的存量流水执行 AI 重新分类，返回处理统计

    scope: unmatched=仅分类为"其他"的流水（默认，量小费用低）；all=全部流水。
    未配置密钥抛 AIClientError（由路由转为 400）；已有任务进行中时同样拒绝重入。
    """
    if not _CLASSIFY_LOCK.acquire(blocking=False):
        raise AIClientError("已有智能分类任务在进行中，请等待完成后再试")
    try:
        return _reclassify_bills_locked(user_id, scope)
    finally:
        _CLASSIFY_LOCK.release()


def _reclassify_bills_locked(user_id: str, scope: str) -> dict:
    settings = load_ai_settings()
    if not settings.ready:
        raise AIClientError(
            "尚未配置 DeepSeek API Key，请先在设置页填写",
            code=ErrorCode.AI_NOT_CONFIGURED,
        )
    bills = BillDAO.list_for_classify(
        user_id, only_unmatched=(scope != "all"), limit=CLASSIFY_LIMIT
    )
    if not bills:
        return {
            "processed": 0,
            "changed": 0,
            "message": "没有需要归类的流水" if scope != "all" else "当前账号没有流水",
        }
    categories = [c["name"] for c in CategoryDAO.list_all()]
    records = [
        {
            "merchant": b["merchant"],
            "remark": b["remark"],
            "tx_type": b["tx_type"],
            "amount": b["amount"],
        }
        for b in bills
    ]
    assignments, completed = classify_batches(
        records, categories, settings, time_budget=CLASSIFY_TIME_BUDGET
    )
    updates = {
        bills[idx]["id"]: category
        for idx, category in assignments.items()
        if bills[idx]["category"] != category
    }
    changed = BillDAO.update_categories(updates, user_id)
    message = ""
    if not completed:
        message = (
            f"达到单次时间预算，本轮已归类 {len(assignments)}/{len(records)} 条，"
            "请再次点击继续归类剩余流水"
        )
    logger.info(
        "账号 %s AI 智能分类完成：%s 条待归类，改写 %s 条分类（scope=%s）",
        user_id,
        len(bills),
        changed,
        scope,
    )
    return {"processed": len(bills), "changed": changed, "message": message}


# ---- AI 月度消费报告 ----

_REPORT_SYSTEM_PROMPT = (
    "你是专业的个人财务分析师。用户会提供某月收支统计数据（含环比上月、分类支出、"
    "商户排行等），请写一份简明的月度消费分析报告（Markdown 格式，简体中文，"
    "500 字以内），包含：总体概览、消费结构亮点、与上月的变化、下月消费建议。"
    "不要编造数据之外的信息，语气务实。"
    '输出 JSON 对象：{"report": "<markdown 文本>"}。'
)


def report_context(user_id: str, month: str) -> dict:
    """收集某月报告所需的统计数据（纯数据，便于测试）"""
    start, end = month_range(month)
    prev = _prev_month(month)
    prev_start, prev_end = month_range(prev)

    this_summary = StatDAO.summary(user_id, start=start, end=end)
    prev_summary = StatDAO.summary(user_id, start=prev_start, end=prev_end)

    top_merchants = StatDAO.merchant_top(user_id, start=start, end=end, limit=5)
    max_expense = 0.0
    max_day = ""
    for row in StatDAO.daily_totals(user_id, start=start, end=end):
        if row["expense"] > max_expense:
            max_expense, max_day = row["expense"], row["date"]

    return {
        "month": month,
        "prev_month": prev,
        "this_income": _round2(this_summary["income"]),
        "this_expense": _round2(this_summary["expense"]),
        "prev_income": _round2(prev_summary["income"]),
        "prev_expense": _round2(prev_summary["expense"]),
        "categories": [
            {"name": r["name"], "expense": _round2(r["value"])}
            for r in StatDAO.category_pie(user_id, start=start, end=end)
        ],
        "prev_categories": {
            r["name"]: _round2(r["value"])
            for r in StatDAO.category_pie(user_id, start=prev_start, end=prev_end)
        },
        "top_merchants": [
            {
                "merchant": r["merchant"],
                "amount": _round2(r["amount"]),
                "count": r["count"],
            }
            for r in top_merchants
        ],
        "max_expense_day": max_day,
        "max_expense": _round2(max_expense),
    }


def _build_report_prompt(ctx: dict) -> str:
    """把统计数据渲染为报告请求的用户消息"""

    def join_or(items: list[str]) -> str:
        return "、".join(items) if items else "无"

    lines = [
        f"统计月份：{ctx['month']}（上月为 {ctx['prev_month']}）",
        f"本月收入 {ctx['this_income']} 元，支出 {ctx['this_expense']} 元；"
        f"上月收入 {ctx['prev_income']} 元，支出 {ctx['prev_expense']} 元。",
        "本月分类支出："
        + join_or([f"{c['name']} {c['expense']}元" for c in ctx["categories"]]),
        "上月分类支出："
        + join_or(
            [f"{name} {value}元" for name, value in ctx["prev_categories"].items()]
        ),
        "商户支出 TOP5："
        + join_or(
            [
                f"{m['merchant']} {m['amount']}元({m['count']}笔)"
                for m in ctx["top_merchants"]
            ]
        ),
        f"单日最高支出：{ctx['max_expense']} 元（{ctx['max_expense_day'] or '无'}）",
        "请生成月度消费分析报告。",
    ]
    return "\n".join(lines)


def _parse_report(content: str) -> str:
    """解析 AI 返回 JSON 中的 report 字段；兼容 ``` 围栏"""
    text = content.strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        inner = parts[1] if len(parts) > 1 else text
        if inner.startswith("json"):
            inner = inner[4:]
        text = inner.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIClientError(f"AI 返回内容不是有效 JSON：{content[:120]}") from exc
    report = data.get("report") if isinstance(data, dict) else None
    if not isinstance(report, str) or not report.strip():
        raise AIClientError("AI 返回 JSON 缺少 report 字段")
    return report.strip()


def generate_month_report(user_id: str, month: str | None = None) -> dict:
    """用 DeepSeek 生成某月消费分析报告；未配置密钥或月份非法抛 AIClientError"""
    settings = load_ai_settings()
    if not settings.ready:
        raise AIClientError(
            "尚未配置 DeepSeek API Key，请先在设置页填写",
            code=ErrorCode.AI_NOT_CONFIGURED,
        )
    if not month:
        today = date.today()
        month = _prev_month(f"{today.year}-{today.month:02d}")
    month = month.strip()
    if not valid_month(month):
        raise AIClientError("无效的月份格式，应为 YYYY-MM")
    ctx = report_context(user_id, month)
    content = _chat(
        settings,
        [
            {"role": "system", "content": _REPORT_SYSTEM_PROMPT},
            {"role": "user", "content": _build_report_prompt(ctx)},
        ],
        max_tokens=2500,
    )
    report = _parse_report(content)
    logger.info("账号 %s 生成 %s 月度消费报告（%s 字）", user_id, month, len(report))
    return {"month": month, "report": report}
