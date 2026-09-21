"""DeepSeek 智能分类：调用 DeepSeek Chat API 对流水做语义归类

设计要点：
- 关键词归类（category_matcher）先执行且零成本，AI 只兜底关键词未命中的记录，
  控制调用量与费用
- DeepSeek 接口为 OpenAI 兼容协议，用标准库 urllib 调用，不新增运行时依赖
- AI 返回的分类必须出现在候选分类中才采纳，否则保留原分类（防幻觉编造分类）
- 批量请求按批容错：单批失败只影响当批记录，导入/重分类主流程不受影响
- 报告生成支持 4 种周期（月/季/半年/年），所有数字由后端算好喂给模型，
  模型只做解释；归档按 (user_id, period_type, period_value) 唯一键 upsert
"""

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from app.config import DEFAULT_CATEGORY
from app.file_settings import AISettings, ai_provider_label, load_ai_settings
from app.core.constants import TX_TYPE_LABELS
from app.core.errors import BizError, ErrorCode, NotFoundError
from app.db.dao.ai_report_dao import AIReportDAO
from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from app.db.dao.stat_dao import StatDAO
from app.schemas.ai import AITestResult
from app.services import notify_service
from app.utils.amount import round2 as _round2
from app.utils.text import strip_code_fence
from app.utils.period import (
    PERIOD_TYPES,
    default_period_value,
    period_label,
    period_range,
    prev_period,
    valid_month,
    valid_period,
)

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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止跟随重定向：Authorization 头（共享 API Key）只发往管理员配置的
    base_url 本身，防止被 30x 转发到其他主机造成密钥外泄（SSRF 加固）。
    DeepSeek 及各兼容端点均不依赖重定向；配置了会跳转的地址会直接收到
    明确的调用失败提示，提示用户改配最终地址。

    必须显式重写 redirect_request 返回 None——只继承不重写时 urllib 仍按
    默认策略跟随 3xx，且 Authorization 头会原样发往跳转目标（安全审计
    实测泄漏）。返回 None 使 3xx 以 HTTPError 抛出，由 _chat 的既有错误
    处理转成可读提示。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _open(request: urllib.request.Request, timeout: float):
    """AI 接口专用打开器（不跟随重定向）；测试经 monkeypatch 替换本函数"""
    return _OPENER.open(request, timeout=timeout)


_SYSTEM_PROMPT = (
    "你是个人记账分类助手。用户会给出若干笔交易（编号、商户、备注、类型、金额）"
    "和候选消费分类，请为每笔交易从候选分类中选出最合适的一个。"
    "只能使用候选分类中的名称，不要发明新分类。"
    '输出 JSON 对象：{"result": {"<编号>": "<分类名>"}}，编号必须与输入一致，'
    "每笔交易都必须给出分类。"
)


def _chat(
    settings: AISettings,
    messages: list[dict],
    max_tokens: int,
    timeout: float = REQUEST_TIMEOUT,
) -> str:
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
        with _open(request, timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = str(body.get("error", {}).get("message") or body)
        except Exception:
            detail = str(exc.reason)
        label = ai_provider_label(settings.provider)
        raise AIClientError(f"{label} 接口返回 {exc.code}：{detail}") from exc
    except TimeoutError as exc:
        raise AIClientError(
            f"{ai_provider_label(settings.provider)} 请求超时（>{timeout}s）"
        ) from exc
    except urllib.error.URLError as exc:
        raise AIClientError(
            f"无法连接 {ai_provider_label(settings.provider)} 服务：{exc.reason}"
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AIClientError(
            f"{ai_provider_label(settings.provider)} 响应不是有效 JSON"
        ) from exc
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIClientError(
            f"{ai_provider_label(settings.provider)} 响应格式异常：{json.dumps(data, ensure_ascii=False)[:200]}"
        ) from exc
    if not isinstance(content, str):
        raise AIClientError("DeepSeek 响应缺少消息内容")
    return content


def chat(
    settings: AISettings,
    messages: list[dict],
    max_tokens: int,
    timeout: float = REQUEST_TIMEOUT,
) -> str:
    """对话调用公开入口（nl_query 意图翻译复用同一通道与 SSRF 防线）

    与 _chat 唯一区别是可指定超时——意图翻译是交互路径，超时须远小于
    报告生成/批量归类（见 nl_query.LLM_TIMEOUT）。
    """
    return _chat(settings, messages, max_tokens, timeout=timeout)


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
        type_label = TX_TYPE_LABELS.get(rec.get("tx_type"), "支出")
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
        try:
            data = json.loads(strip_code_fence(content))
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


def reclassify_bills(
    user_id: str, scope: str = "unmatched", after_id: Optional[int] = None
) -> dict:
    """对当前账号的存量流水执行 AI 重新分类，返回处理统计

    scope: unmatched=仅分类为"其他"的流水（默认，量小费用低）；all=全部流水。
    after_id: scope=all 的续跑游标（见 _reclassify_bills_locked）。
    未配置密钥抛 AIClientError（由路由转为 400）；已有任务进行中时同样拒绝重入。
    """
    if not _CLASSIFY_LOCK.acquire(blocking=False):
        raise AIClientError("已有智能分类任务在进行中，请等待完成后再试")
    try:
        return _reclassify_bills_locked(user_id, scope, after_id)
    finally:
        _CLASSIFY_LOCK.release()


def _reclassify_bills_locked(
    user_id: str, scope: str, after_id: Optional[int] = None
) -> dict:
    settings = load_ai_settings()
    if not settings.ready:
        raise AIClientError(
            "尚未配置 DeepSeek API Key，请先在设置页填写",
            code=ErrorCode.AI_NOT_CONFIGURED,
        )
    # scope=all 用游标续跑：固定取前 N 条会让第 N 条之后的流水经此路径永远
    # 不可达，且时间预算耗尽后「再次点击」会从第 0 条重复调 API 重复计费。
    # unmatched 不需要游标——归类后的流水离开「其他」筛选，重扫天然前进，
    # 还能顺带重试此前归类失败的行。
    use_cursor = scope == "all"
    bills = BillDAO.list_for_classify(
        user_id,
        only_unmatched=(scope != "all"),
        limit=CLASSIFY_LIMIT,
        after_id=after_id if use_cursor else None,
    )
    if not bills:
        return {
            "processed": 0,
            "changed": 0,
            "message": "没有需要归类的流水" if scope != "all" else "当前账号没有流水",
            "completed": True,
            "next_after_id": None,
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

    full_page = len(bills) >= CLASSIFY_LIMIT
    message = ""
    if use_cursor:
        # 游标推进到最后一条「实际处理过」的流水：预算中途耗尽时未处理的
        # 尾部不从游标跳过，下次仍会覆盖到
        if assignments:
            next_after_id = bills[max(assignments.keys())]["id"]
        else:
            next_after_id = after_id
        completed = bool(completed) and not full_page
        if not completed:
            if assignments:
                message = f"本轮已归类 {len(assignments)} 条，剩余流水请再次点击继续"
            else:
                message = "本轮未处理任何流水（时间预算或额度限制），请稍后再次点击"
    else:
        next_after_id = None
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
    return {
        "processed": len(bills),
        "changed": changed,
        "message": message,
        "completed": completed,
        "next_after_id": next_after_id,
    }


# ---- AI 周期消费报告（月/季/半年/年）+ 归档 ----

_REPORT_SYSTEM_PROMPT = (
    "你是专业的个人财务分析师。用户会提供某周期（月/季/半年/年）收支统计数据"
    "（含与上一周期对比、分类支出、商户排行等），请写一份简明的消费分析报告"
    "（Markdown 格式，简体中文，500 字以内），包含：总体概览、消费结构亮点、"
    "与上一周期的变化、下一周期消费建议。"
    "不要编造数据之外的信息，语气务实。"
    '输出 JSON 对象：{"report": "<markdown 文本>"}。'
)


def report_context_period(user_id: str, period_type: str, period_value: str) -> dict:
    """收集某周期报告所需的统计数据（纯数据，便于测试）

    复用 StatDAO 的 4 个只读方法（summary/category_pie/merchant_top/daily_totals），
    只把 start/end 换成 period_range 计算的周期边界。所有数字由后端算好，
    模型只做解释（与 T-6.5 报告口径可追溯的设计一致）。
    """
    start, end = period_range(period_type, period_value)
    prev_value = prev_period(period_type, period_value)
    prev_start, prev_end = period_range(period_type, prev_value)

    this_summary = StatDAO.summary(user_id, start=start, end=end)
    prev_summary = StatDAO.summary(user_id, start=prev_start, end=prev_end)

    top_merchants = StatDAO.merchant_top(user_id, start=start, end=end, limit=5)
    max_expense = 0.0
    max_day = ""
    for row in StatDAO.daily_totals(user_id, start=start, end=end):
        if row["expense"] > max_expense:
            max_expense, max_day = row["expense"], row["date"]

    return {
        "period_type": period_type,
        "period_value": period_value,
        "period_label": period_label(period_type, period_value),
        "prev_period_value": prev_value,
        "prev_period_label": period_label(period_type, prev_value),
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


def report_context(user_id: str, month: str) -> dict:
    """月度报告统计上下文（旧签名，兼容既有测试与 generate_month_report）

    转发到 report_context_period 并补 month/prev_month 别名，避免重复代码。
    """
    ctx = report_context_period(user_id, "month", month)
    ctx["month"] = month
    ctx["prev_month"] = ctx["prev_period_value"]
    return ctx


def _build_report_prompt(ctx: dict) -> str:
    """把统计数据渲染为报告请求的用户消息

    月度分支保持原「本月/上月」文案不变（兼容现有测试断言 "本月分类支出：..."）；
    季/半年/年用「本期/上期」+ period_label 通用文案，避免月份专属词误用。
    """

    def join_or(items: list[str]) -> str:
        return "、".join(items) if items else "无"

    period_type = ctx.get("period_type", "month")
    if period_type == "month":
        # 兼容旧 ctx（无 period_value 字段）→ 回退到 month/prev_month
        month = ctx.get("period_value") or ctx["month"]
        prev = ctx.get("prev_period_value") or ctx["prev_month"]
        lines = [
            f"统计月份：{month}（上月为 {prev}）",
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

    # 季/半年/年通用文案
    lines = [
        f"统计周期：{ctx['period_label']}（上一周期为 {ctx['prev_period_label']}）",
        f"本期收入 {ctx['this_income']} 元，支出 {ctx['this_expense']} 元；"
        f"上期收入 {ctx['prev_income']} 元，支出 {ctx['prev_expense']} 元。",
        "本期分类支出："
        + join_or([f"{c['name']} {c['expense']}元" for c in ctx["categories"]]),
        "上期分类支出："
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
        f"请生成{ctx['period_label']}消费分析报告。",
    ]
    return "\n".join(lines)


def _parse_report(content: str) -> str:
    """解析 AI 返回 JSON 中的 report 字段；兼容 ``` 围栏"""
    try:
        data = json.loads(strip_code_fence(content))
    except json.JSONDecodeError as exc:
        raise AIClientError(f"AI 返回内容不是有效 JSON：{content[:120]}") from exc
    report = data.get("report") if isinstance(data, dict) else None
    if not isinstance(report, str) or not report.strip():
        raise AIClientError("AI 返回 JSON 缺少 report 字段")
    return report.strip()


def generate_report(
    user_id: str, period_type: str, period_value: str | None = None
) -> dict:
    """用 DeepSeek 生成某周期消费分析报告（生成预览，不落库）

    返回 {period_type, period_value, title, report, context}；context 为统计上下文，
    前端归档时原样回传 stats_summary 字段，无需再算一次。
    未配置密钥 / 周期类型非法 / 周期标识非法抛 AIClientError。
    """
    settings = load_ai_settings()
    if not settings.ready:
        raise AIClientError(
            "尚未配置 DeepSeek API Key，请先在设置页填写",
            code=ErrorCode.AI_NOT_CONFIGURED,
        )
    if period_type not in PERIOD_TYPES:
        raise AIClientError(f"无效的周期类型：{period_type}")
    if not period_value:
        period_value = default_period_value(period_type)
    period_value = period_value.strip()
    if not valid_period(period_type, period_value):
        raise AIClientError(f"无效的周期标识：{period_type}={period_value}")
    ctx = report_context_period(user_id, period_type, period_value)
    content = _chat(
        settings,
        [
            {"role": "system", "content": _REPORT_SYSTEM_PROMPT},
            {"role": "user", "content": _build_report_prompt(ctx)},
        ],
        max_tokens=2500,
    )
    report = _parse_report(content)
    title = f"{ctx['period_label']}消费分析报告"
    logger.info(
        "账号 %s 生成 %s=%s 消费报告（%s 字）",
        user_id,
        period_type,
        period_value,
        len(report),
    )
    # 生成可达数十秒，用户可能已切走：完成后发通知事件（T-5.4，自身吞异常）
    notify_service.notify_report_ready(user_id, title)
    return {
        "period_type": period_type,
        "period_value": period_value,
        "title": title,
        "report": report,
        "context": ctx,
    }


def generate_month_report(user_id: str, month: str | None = None) -> dict:
    """月度报告（旧接口，兼容）：转发到 generate_report 并重塑响应为 {month, report}

    保留独立的月份格式校验，错误文案与旧版一致（"无效的月份格式，应为 YYYY-MM"）。
    """
    if month:
        month = month.strip()
        if not valid_month(month):
            raise AIClientError("无效的月份格式，应为 YYYY-MM")
    result = generate_report(user_id, "month", month)
    # 仅暴露 month + report 两个字段，保持旧 API 响应结构不变
    return {"month": result["period_value"], "report": result["report"]}


# ---- 归档：保存/查询/删除 ----


def archive_report(
    user_id: str,
    period_type: str,
    period_value: str,
    title: str,
    content: str,
    stats_summary: dict | None = None,
) -> dict:
    """归档报告：按 (user_id, period_type, period_value) 唯一键 upsert

    stats_summary 为生成时返回的 context dict，json.dumps 后落库备查（T-6.5 溯源）。
    周期标识不合法抛 AIClientError；并发冲突由 DAO 翻译为 ConflictError。
    """
    if period_type not in PERIOD_TYPES:
        raise AIClientError(f"无效的周期类型：{period_type}")
    if not valid_period(period_type, period_value):
        raise AIClientError(f"无效的周期标识：{period_type}={period_value}")
    summary_str = json.dumps(stats_summary, ensure_ascii=False) if stats_summary else ""
    return AIReportDAO.upsert(
        user_id=user_id,
        period_type=period_type,
        period_value=period_value,
        title=title or f"{period_label(period_type, period_value)}消费分析报告",
        content=content,
        stats_summary=summary_str,
    )


def list_archived(user_id: str, period_type: str | None = None) -> list[dict]:
    """列出归档报告（仅当前账号）；period_type 过滤可选"""
    if period_type and period_type not in PERIOD_TYPES:
        raise AIClientError(f"无效的周期类型：{period_type}")
    return AIReportDAO.list_all(user_id, period_type)


def get_archived(user_id: str, report_id: int) -> dict:
    """查询单条归档报告（含 content / stats_summary）；不存在抛 NotFoundError"""
    report = AIReportDAO.get(user_id, report_id)
    if report is None:
        raise NotFoundError("归档报告不存在")
    return report


def delete_archived(user_id: str, report_id: int) -> bool:
    """删除归档报告（仅当前账号）；不存在返回 False"""
    return AIReportDAO.delete(user_id, report_id)
