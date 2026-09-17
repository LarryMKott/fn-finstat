"""自然语言查账意图翻译层（T-6.1）：把一句话问题翻译成结构化查询并执行

两段式设计（见开发计划）：
1. **规则优先**：正则/关键词解析高频问法（时间 + 指标 + 分类 + 商户 + 分组维度），
   命中即用——零成本、零延迟、零幻觉；
2. **LLM 兜底**：未命中时交模型做意图翻译，仅产出结构化查询对象（JSON），
   不参与任何计算，也不生成答案文本。

安全红线（不可妥协）：
- 结构化查询对象的每个字段都经白名单校验（枚举/真实分类表/长度钳制），
  模型输出出现非白名单字段一律忽略并记日志，绝不拼接进 SQL；
- 查询只走 StatDAO/BillDAO 的只读方法，user_id 由服务端强制注入，
  接口层不接受任何身份/越权参数（schema extra="forbid"）；
- 结果集上限（分组 ≤100、明细 ≤20、问题 ≤200 字，schema 层再限一次）；
- 模型不可用 / 调用失败 / 超出单日调用配额时降级为规则路径，
  并在 message 中明确告知用户（绝不静默假装一切正常）。

口径透明：答案文本由后端按查询结果确定性生成（数字全部来自数据库），
时间范围 / 筛选条件 / 覆盖笔数随结果回传，前端必须原样展示（T-6.2）。
"""

import json
import logging
import re
import threading
from datetime import date, timedelta

from app.config import DEFAULT_CATEGORY, load_ai_settings
from app.core.errors import ValidationError
from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from app.db.dao.stat_dao import StatDAO
from app.services.ai_service import AIClientError, chat
from app.utils.amount import round2
from app.utils.period import period_label, period_range, prev_period

logger = logging.getLogger(__name__)

# 意图翻译是交互路径（验收口径 <8s），超时须远小于报告生成/批量归类
LLM_TIMEOUT = 8
# 单日 LLM 意图翻译调用上限（规则路径不计数）：超限自动降级规则模式，
# 是 v0.6「AI 费用有上限」出口标准的一部分
LLM_DAILY_LIMIT = 100
# 明细样本条数上限：口径透明用，不是导出
DETAIL_LIMIT = 20

_METRICS = ("expense", "income", "count")
_GROUP_BYS = ("none", "category", "merchant", "month", "day")
_ORDER_BYS = ("amount_desc", "amount_asc", "count_desc", "count_asc", "key_asc")

_GROUP_LABELS = {"category": "分类", "merchant": "商户", "month": "月份", "day": "日期"}

# ---- 规则路径：时间表达 ----
# 顺序即优先级：越具体的表达越靠前；builder(match, today) -> (start, end, label)，
# 日期非法（如 2 月 30 日）抛 ValueError 视为未命中。

_T_START_YMD = r"(?P<sy>20\d{2})[年./-](?P<sm>\d{1,2})[月./-](?P<sd>\d{1,2})[日号]?"
_T_END_YMD = r"(?P<ey>20\d{2})[年./-](?P<em>\d{1,2})[月./-](?P<ed>\d{1,2})[日号]?"
_T_START_MD = r"(?:(?P<sy>20\d{2})年)?(?P<sm>\d{1,2})月(?P<sd>\d{1,2})[日号]"
_T_END_MD = r"(?:(?P<ey>20\d{2})年)?(?P<em>\d{1,2})月(?P<ed>\d{1,2})[日号]"
# 区间分隔符不含 "-"：ISO 日期自身含 "-"，混用会产生歧义切分
_T_SEP = r"\s*[到至~～—–]\s*"


def _h_ymd_range(m: re.Match, today: date) -> tuple[str, str, str]:
    start = date(int(m["sy"]), int(m["sm"]), int(m["sd"]))
    end = date(int(m["ey"]), int(m["em"]), int(m["ed"]))
    if start > end:
        start, end = end, start
    return (
        start.isoformat(),
        end.isoformat(),
        f"{start.isoformat()} 至 {end.isoformat()}",
    )


def _h_md_range(m: re.Match, today: date) -> tuple[str, str, str]:
    year = int(m["sy"]) if m["sy"] else int(m["ey"] or today.year)
    start = date(year, int(m["sm"]), int(m["sd"]))
    end = date(int(m["ey"]) or year, int(m["em"]), int(m["ed"]))
    if end < start:  # 跨年区间（12月30日到1月2日）按顺延一年处理
        end = date(end.year + 1, end.month, end.day)
    return (
        start.isoformat(),
        end.isoformat(),
        f"{start.isoformat()} 至 {end.isoformat()}",
    )


def _h_ymd_single(m: re.Match, today: date) -> tuple[str, str, str]:
    day = date(int(m["y"]), int(m["m"]), int(m["d"]))
    return day.isoformat(), day.isoformat(), day.isoformat()


def _h_md_single(m: re.Match, today: date) -> tuple[str, str, str]:
    day = date(int(m["y"]) if m["y"] else today.year, int(m["m"]), int(m["d"]))
    return day.isoformat(), day.isoformat(), day.isoformat()


def _h_iso_month(m: re.Match, today: date) -> tuple[str, str, str]:
    value = f"{int(m['y']):04d}-{int(m['m']):02d}"
    start, end = period_range("month", value)
    return start, end, period_label("month", value)


def _h_year_month(m: re.Match, today: date) -> tuple[str, str, str]:
    if m["y"]:
        year = int(m["y"])
    elif m["kw"] == "今年":
        year = today.year
    elif m["kw"] == "去年":
        year = today.year - 1
    else:  # 前年 / 裸月份
        year = today.year - 2 if m["kw"] == "前年" else today.year
    value = f"{year:04d}-{int(m['m']):02d}"
    start, end = period_range("month", value)
    return start, end, period_label("month", value)


def _h_year(m: re.Match, today: date) -> tuple[str, str, str]:
    value = str(int(m["y"]))
    start, end = period_range("year", value)
    return start, end, period_label("year", value)


def _h_half(m: re.Match, today: date) -> tuple[str, str, str]:
    if m["y"]:
        year = int(m["y"])
    elif m["kw"] == "去年":
        year = today.year - 1
    elif m["kw"] == "前年":
        year = today.year - 2
    else:
        year = today.year
    value = f"{year:04d}-H{1 if m['h'] == '上半年' else 2}"
    start, end = period_range("half", value)
    return start, end, period_label("half", value)


def _h_quarter(m: re.Match, today: date, offset: int = 0) -> tuple[str, str, str]:
    q = (today.month - 1) // 3 + 1
    value = (
        prev_period("quarter", f"{today.year:04d}-Q{q}")
        if offset
        else f"{today.year:04d}-Q{q}"
    )
    start, end = period_range("quarter", value)
    return start, end, period_label("quarter", value)


def _h_relative_month(m: re.Match, today: date, offset: int) -> tuple[str, str, str]:
    year, mon = today.year, today.month
    for _ in range(offset):
        year, mon = (year - 1, 12) if mon == 1 else (year, mon - 1)
    value = f"{year:04d}-{mon:02d}"
    start, end = period_range("month", value)
    return start, end, period_label("month", value)


def _h_last_days(m: re.Match, today: date) -> tuple[str, str, str]:
    n = max(1, min(365, int(m["n"])))
    start = (today - timedelta(days=n - 1)).isoformat()
    end = today.isoformat()
    return start, end, f"最近 {n} 天"


def _h_week(m: re.Match, today: date, weeks_ago: int = 0) -> tuple[str, str, str]:
    monday = today - timedelta(days=today.weekday() + 7 * weeks_ago)
    sunday = monday + timedelta(days=6)
    return (
        monday.isoformat(),
        sunday.isoformat(),
        ("本周" if weeks_ago == 0 else "上周"),
    )


_TIME_TABLE: list[tuple[re.Pattern[str], object]] = [
    (re.compile(rf"{_T_START_YMD}{_T_SEP}{_T_END_YMD}"), _h_ymd_range),
    (re.compile(rf"{_T_START_MD}{_T_SEP}{_T_END_MD}"), _h_md_range),
    (
        re.compile(r"(?P<y>20\d{2})[年./-](?P<m>\d{1,2})[月./-](?P<d>\d{1,2})[日号]?"),
        _h_ymd_single,
    ),
    (
        re.compile(r"(?:(?P<y>20\d{2})年)?(?P<m>\d{1,2})月(?P<d>\d{1,2})[日号]"),
        _h_md_single,
    ),
    # ISO 月份（2026-08）：否定前瞻避免咬到完整 ISO 日期的片段
    (
        re.compile(r"(?<![\d-])(?P<y>20\d{2})-(?P<m>1[0-2]|0?[1-9])(?![\d-])"),
        _h_iso_month,
    ),
    (
        re.compile(
            r"(?:(?P<y>20\d{2})年|(?P<kw>今年|去年|前年))?(?P<m>1[0-2]|0?[1-9])月份?"
        ),
        _h_year_month,
    ),
    (re.compile(r"(?P<y>20\d{2})年(?:度|份)?"), _h_year),
    (
        re.compile(r"(?:(?P<y>20\d{2})年|(?P<kw>今年|去年|前年))?(?P<h>上半年|下半年)"),
        _h_half,
    ),
    (re.compile(r"本季度|这个季度"), lambda m, t: _h_quarter(m, t, 0)),
    (re.compile(r"上个季度|上季度"), lambda m, t: _h_quarter(m, t, 1)),
    (re.compile(r"上上个月|上上月"), lambda m, t: _h_relative_month(m, t, 2)),
    (re.compile(r"上个月|上月"), lambda m, t: _h_relative_month(m, t, 1)),
    (re.compile(r"本月|这个月|当月|这月"), lambda m, t: _h_relative_month(m, t, 0)),
    (
        re.compile(r"最近一个月|近一个月|最近1个?月|近1个?月"),
        lambda m, t: _h_last_days({"n": 30}, t),
    ),
    (re.compile(r"(?:最近|近|过去)(?P<n>\d{1,3})天"), _h_last_days),
    (re.compile(r"本周|这一周|这周"), lambda m, t: _h_week(m, t, 0)),
    (re.compile(r"上周"), lambda m, t: _h_week(m, t, 1)),
    (re.compile(r"今年|本年|本年度"), lambda m, t: _h_year({"y": str(t.year)}, t)),
    (re.compile(r"去年"), lambda m, t: _h_year({"y": str(t.year - 1)}, t)),
    (re.compile(r"前年"), lambda m, t: _h_year({"y": str(t.year - 2)}, t)),
]


def _parse_time(
    text: str, today: date
) -> tuple[tuple[str | None, str | None, str], tuple[int, int]] | None:
    """解析时间表达，返回 ((start, end, label), 命中区间)；未命中返回 None

    命中区间供调用方从原句剔除时间词，避免后续商户提取把"8月"当成商户。
    """
    for pattern, handler in _TIME_TABLE:
        m = pattern.search(text)
        if m is None:
            continue
        try:
            start, end, label = handler(m, today)
        except (ValueError, ValidationError):
            continue
        return (start, end, label), (m.start(), m.end())
    return None


# ---- 规则路径：指标 / 分组 / TOP N ----

_METRIC_COUNT = re.compile(r"多少笔|几笔|笔数|多少条|几条|多少次|几次|多少单|几单")
_METRIC_INCOME = re.compile(r"收入|进账|入账|挣|赚|到账|工资|薪水")

_GROUP_HINTS: list[tuple[str, re.Pattern[str]]] = [
    (
        "category",
        re.compile(
            r"各分类|按分类|分类(分布|占比|统计|排行|排名|明细|支出|消费)|哪些分类"
        ),
    ),
    (
        "merchant",
        re.compile(
            r"各商户|按商户|商户(排行|排名|分布|统计)|哪个商户|哪家店|哪家商户|哪家商家|各商家|按商家|商家(排行|排名)"
        ),
    ),
    ("month", re.compile(r"按月|每月|各月|逐月|月度趋势|月趋势")),
    ("day", re.compile(r"按天|按日|每日|每天|逐日|日趋势")),
]
_TOP_N = re.compile(r"[tT][oO][pP]\s*(?P<n>\d{1,3})")
_PRE_N = re.compile(r"前\s*(?P<n>\d{1,3})\s*(?:名|个|位|笔|条|项)")
_RANK_HINT = re.compile(r"排行|排名")
# 比较类问法（"A和B哪个多"）单条查询对象表达不了，规则路径主动让位给 LLM
# 翻译（常见解法是拉长时间窗并按月/分类分组）；降级时仍用规则口径兜底回答
_COMPARISON = re.compile(
    r"哪个月|哪一年|哪年|哪个季度|哪周|哪天|哪一天|哪个多|对比|比较|环比"
)


# ---- 规则路径：商户关键词提取（分类/时间词剔除后做兜底启发式）----

_MERCHANT_PATTERNS = [
    # 引号里的明确商户名
    re.compile(r"[「“\"‘']([^」”\"’']{2,24})[」”\"’']"),
    # 在/去/到 + 商户 + 动词
    re.compile(
        r"[在去到]\s*([\u4e00-\u9fa5A-Za-z0-9]{2,16}?)\s*"
        r"(?:那里|买了|花|消费|充值|充了|付款|付了|付|吃|喝|买)"
    ),
    # 给 + 商户 + 动词
    re.compile(
        r"给\s*([\u4e00-\u9fa5A-Za-z0-9]{2,16}?)\s*(?:花了|花过|充了|充值|付了|转了|转账)"
    ),
    # XX 的花费/支出/钱
    re.compile(r"([\u4e00-\u9fa5A-Za-z0-9]{2,16}?)的(?:花费|支出|消费|开销|钱)"),
    # XX + 花了/消费了（兜底，需前置剔除时间与分类词）
    re.compile(
        r"([\u4e00-\u9fa5A-Za-z0-9]{2,16}?)(?:花了|花掉|花费了|消费了|支付了|消费|花费)"
    ),
]
# 候选清洗：剔除动词性前缀（单字，避免误伤"和府捞面"这类品牌首字）
_MERCHANT_LEAD = re.compile(r"^[在给从去到]")
_MERCHANT_TAIL = re.compile(r"[的那里上]+$")
# 候选含时间/疑问/汇总等字样即拒绝（商户名几乎不会包含这些字）
_MERCHANT_STOP_CHARS = re.compile(
    r"[年月日周号点分秒的按各每]|哪|多少|几|怎么|吗|呢|一共|总共|所有|全部|统计|报表|排行|趋势|分布|占比|平均|最"
)
_MERCHANT_STOPWORDS = {
    "东西",
    "产品",
    "费用",
    "钱",
    "金额",
    "账单",
    "消费",
    "支出",
    "收入",
    "花销",
    "开销",
    "我",
    "它",
    "他",
    "她",
    "大家",
    "别人",
}


def _clean_merchant(raw: str, category_set: set[str]) -> str:
    cand = _MERCHANT_TAIL.sub("", _MERCHANT_LEAD.sub("", raw.strip()))
    if not 2 <= len(cand) <= 24:
        return ""
    if cand.isdigit() or cand in _MERCHANT_STOPWORDS or cand in category_set:
        return ""
    if _MERCHANT_STOP_CHARS.search(cand):
        return ""
    return cand


def _extract_merchants(text: str, category_set: set[str]) -> list[str]:
    merchants: list[str] = []
    for pattern in _MERCHANT_PATTERNS:
        for m in pattern.finditer(text):
            cand = _clean_merchant(m.group(1), category_set)
            if cand and cand not in merchants:
                merchants.append(cand)
                if len(merchants) >= 3:
                    return merchants
    return merchants


# ---- 结构化查询对象（口径） ----


def _default_label(start: str | None, end: str | None) -> str:
    if start and end:
        return f"{start} 至 {end}" if start != end else start
    return "全部时间"


def _make_spec(
    time_tuple: tuple[str | None, str | None, str],
    categories: list[str],
    merchants: list[str],
    metric: str,
    group_by: str,
    order_by: str,
    limit: int,
) -> dict:
    start, end, label = time_tuple
    return {
        "time": {
            "start": start,
            "end": end,
            "label": label or _default_label(start, end),
        },
        "categories": categories,
        "merchants": merchants,
        "metric": metric,
        "group_by": group_by,
        "order_by": order_by,
        "limit": limit,
    }


def _rule_parse(text: str, today: date, category_set: set[str]) -> tuple[dict, bool]:
    """规则路径解析：返回 (结构化查询对象, 是否命中)

    命中 = 至少解析出时间 / 分类 / 商户 / 分组维度之一；纯问句（"花了多少"）
    不算命中，交给 LLM 兜底（未配置模型时降级用全量口径回答并明确提示）。
    分类匹配只认真实分类名（长度优先），「其他」是兜底分类也是高频口语词，
    规则路径不做匹配（LLM 路径仍可选它）。
    """
    cleaned = text
    time_tuple = (None, None, "")
    parsed_time = _parse_time(cleaned, today)
    if parsed_time is not None:
        time_tuple, (s, e) = parsed_time
        cleaned = cleaned[:s] + "，" + cleaned[e:]

    metric = "expense"
    if _METRIC_COUNT.search(cleaned):
        metric = "count"
    elif _METRIC_INCOME.search(cleaned):
        metric = "income"

    group_by = "none"
    for hint, pattern in _GROUP_HINTS:
        if pattern.search(cleaned):
            group_by = hint
            break

    order_by, limit, top_hit = "amount_desc", 10, False
    m = _TOP_N.search(cleaned) or _PRE_N.search(cleaned)
    if m:
        limit = max(1, min(100, int(m.group("n"))))
        top_hit = True
    if top_hit and group_by == "none":
        group_by = "merchant"
    if group_by == "none" and _RANK_HINT.search(cleaned):
        group_by = "merchant"

    categories: list[str] = []
    for name in sorted(
        (n for n in category_set if n and n != DEFAULT_CATEGORY), key=len, reverse=True
    ):
        if name in cleaned:
            categories.append(name)
            cleaned = cleaned.replace(name, "，")
            if len(categories) >= 3:
                break

    merchants = _extract_merchants(cleaned, category_set)
    hit = bool(
        time_tuple[0] or categories or merchants or top_hit or group_by != "none"
    ) and not _COMPARISON.search(text)
    return (
        _make_spec(
            time_tuple, categories, merchants, metric, group_by, order_by, limit
        ),
        hit,
    )


# ---- LLM 兜底：意图翻译（只产出查询对象，不参与计算） ----

_LLM_SYSTEM_TEMPLATE = (
    "你是记账应用的查询解析器。把用户的自然语言问题翻译成一个 JSON 查询对象，"
    "不要回答问题本身，不要输出字段以外的内容。字段定义：\n"
    '- time: {{"start": "YYYY-MM-DD" 或 null, "end": "YYYY-MM-DD" 或 null, '
    '"label": "时间口径的简短描述"}}；边界按含当日语义，null 表示不限\n'
    "- categories: 字符串数组，只能从这些真实分类中选择：{categories}，无关则空数组\n"
    "- merchants: 商户关键词字符串数组（按子串匹配，每项 1-32 字），无则空数组\n"
    '- metric: "expense"（支出总额）| "income"（收入总额）| "count"（流水笔数）；'
    "问消费/花费默认 expense\n"
    '- group_by: "none" | "category" | "merchant" | "month" | "day"；'
    "仅当用户要求分组/排行/分布/趋势时才不是 none\n"
    '- order_by: "amount_desc" | "amount_asc" | "count_desc" | "count_asc" | "key_asc"\n'
    "- limit: 1-100 的整数，分组返回的组数上限，默认 10\n"
    "今天的日期是 {today}。只能输出一个 JSON 对象。"
)

_SPEC_KEYS = {
    "time",
    "categories",
    "merchants",
    "metric",
    "group_by",
    "order_by",
    "limit",
}


def _llm_messages(question: str, category_names: list[str], today: date) -> list[dict]:
    system = _LLM_SYSTEM_TEMPLATE.format(
        categories=json.dumps(category_names, ensure_ascii=False),
        today=today.isoformat(),
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]


def _loads_loose(content: str):
    """解析模型返回的 JSON；兼容 ``` 围栏包裹"""
    text = content.strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        inner = parts[1] if len(parts) > 1 else text
        if inner.startswith("json"):
            inner = inner[4:]
        text = inner.strip()
    return json.loads(text)


def _safe_date(value) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        logger.info("NL 查询模型输出非法日期，已丢弃：%r", value)
        return None


def _enum_or(raw, allowed: tuple[str, ...], default: str) -> str:
    if raw in allowed:
        return raw
    if raw not in (None, ""):
        logger.info("NL 查询模型输出非法枚举 %r，回退 %s", raw, default)
    return default


def _normalize_llm_spec(raw, valid_categories: set[str], today: date) -> dict:
    """把模型输出收敛为白名单内的结构化查询对象（安全红线的落地处）"""
    if not isinstance(raw, dict):
        raise ValueError("模型输出不是 JSON 对象")
    unknown = set(raw) - _SPEC_KEYS
    if unknown:
        logger.warning(
            "NL 查询模型输出包含非白名单字段，已忽略：%s", sorted(map(str, unknown))
        )

    time_raw = raw.get("time")
    time_raw = time_raw if isinstance(time_raw, dict) else {}
    start = _safe_date(time_raw.get("start"))
    end = _safe_date(time_raw.get("end"))
    if start and end and start > end:
        start, end = end, start
    label = time_raw.get("label")
    label = label.strip() if isinstance(label, str) else ""

    categories: list[str] = []
    raw_categories = raw.get("categories")
    if isinstance(raw_categories, list):
        for item in raw_categories:
            name = str(item).strip()
            if not name:
                continue
            if name in valid_categories and name not in categories:
                categories.append(name)
            elif name not in valid_categories:
                logger.info("NL 查询模型输出分类不在真实分类表，已丢弃：%r", name)
            if len(categories) >= 3:
                break

    merchants: list[str] = []
    raw_merchants = raw.get("merchants")
    if isinstance(raw_merchants, list):
        for item in raw_merchants:
            merchant = str(item).strip()[:64]
            if merchant and merchant not in merchants:
                merchants.append(merchant)
            if len(merchants) >= 3:
                break

    raw_limit = raw.get("limit")
    limit = 10
    if isinstance(raw_limit, (int, float)) and not isinstance(raw_limit, bool):
        limit = max(1, min(100, int(raw_limit)))

    return _make_spec(
        (start, end, label),
        categories,
        merchants,
        _enum_or(raw.get("metric"), _METRICS, "expense"),
        _enum_or(raw.get("group_by"), _GROUP_BYS, "none"),
        _enum_or(raw.get("order_by"), _ORDER_BYS, "amount_desc"),
        limit,
    )


# ---- 单日 LLM 调用配额（进程内计数，超限自动降级规则模式） ----

_quota_lock = threading.Lock()
_quota_used: dict[str, int] = {}


def _llm_quota_left(today: date) -> int:
    with _quota_lock:
        return max(0, LLM_DAILY_LIMIT - _quota_used.get(today.isoformat(), 0))


def _record_llm_call(today: date) -> None:
    with _quota_lock:
        key = today.isoformat()
        _quota_used[key] = _quota_used.get(key, 0) + 1
        # 只保留近两天的计数，防长期驻留膨胀
        for stale in [k for k in _quota_used if k < key]:
            _quota_used.pop(stale, None)


# ---- 查询执行与答案生成（数字全部来自数据库） ----


def _execute(user_id: str, spec: dict) -> tuple[dict, list[dict]]:
    tx_type = {"expense": "expense", "income": "income", "count": None}[spec["metric"]]
    kwargs = {
        "start": spec["time"]["start"],
        "end": spec["time"]["end"],
        "tx_type": tx_type,
        "categories": spec["categories"] or None,
        "merchants": spec["merchants"] or None,
    }
    agg = StatDAO.nl_aggregate(
        user_id,
        group_by=None if spec["group_by"] == "none" else spec["group_by"],
        order_by=spec["order_by"],
        limit=spec["limit"],
        **kwargs,
    )
    details = BillDAO.nl_detail_rows(user_id, limit=DETAIL_LIMIT, **kwargs)
    return agg, details


def _caliber(spec: dict) -> str:
    parts = [spec["time"]["label"] or "全部时间"]
    if spec["categories"]:
        parts.append("分类 " + "、".join(spec["categories"]))
    if spec["merchants"]:
        parts.append("商户含 " + "、".join(spec["merchants"]))
    return "，".join(parts)


def _build_answer(spec: dict, total: float, count: int) -> str:
    caliber = _caliber(spec)
    if count == 0:
        return f"「{caliber}」没有查询到流水。"
    if spec["metric"] == "count":
        head = f"「{caliber}」共有 {count} 笔流水"
    else:
        word = "支出合计" if spec["metric"] == "expense" else "收入合计"
        head = f"「{caliber}」{word} {round2(total):.2f} 元，共 {count} 笔"
    if spec["group_by"] != "none":
        head += f"（按{_GROUP_LABELS[spec['group_by']]}统计见下方分组）"
    return head + "。"


def _llm_or_fallback(
    text: str, rule_spec: dict, category_names: list[str], today: date
) -> tuple[dict, str, bool, str]:
    """LLM 意图翻译；未配置 / 超配额 / 失败一律降级规则路径并明确告知"""
    settings = load_ai_settings()
    if not settings.ready:
        return rule_spec, "fallback", True, "未配置 AI 模型，本次由规则模式解析"
    if _llm_quota_left(today) <= 0:
        return (
            rule_spec,
            "fallback",
            True,
            f"今日 AI 解析次数已达上限（{LLM_DAILY_LIMIT} 次），本次由规则模式解析",
        )
    try:
        content = chat(
            settings,
            _llm_messages(text, category_names, today),
            max_tokens=400,
            timeout=LLM_TIMEOUT,
        )
        spec = _normalize_llm_spec(_loads_loose(content), set(category_names), today)
    except (AIClientError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("NL 查询 LLM 解析失败，降级规则模式：%s", exc)
        return rule_spec, "fallback", True, "AI 解析失败，本次由规则模式解析"
    _record_llm_call(today)
    return spec, "llm", False, ""


def query(user_id: str, question: str, today: date | None = None) -> dict:
    """一句话查账主入口：翻译意图（规则→LLM）→ 只读执行 → 确定性答案

    user_id 由调用方（路由层 CurrentUser）传入，DAO 层强制注入，
    任何路径都不存在跨账号读取；问题为空抛 ValidationError（400）。
    """
    today = today or date.today()
    text = " ".join(str(question or "").split())
    if not text:
        raise ValidationError("问题不能为空")
    if len(text) > 200:
        raise ValidationError("问题过长，请精简后重试（200 字以内）")

    category_names = [c["name"] for c in CategoryDAO.list_all()]
    rule_spec, hit = _rule_parse(text, today, set(category_names))
    if hit:
        spec, source, degraded, message = rule_spec, "rule", False, ""
    else:
        spec, source, degraded, message = _llm_or_fallback(
            text, rule_spec, category_names, today
        )
    agg, details = _execute(user_id, spec)
    total = agg["total"]
    count = agg["count"]
    rows = agg["rows"]
    truncated = agg["truncated"]
    logger.info(
        "NL 查询（账号 %s，路径 %s，覆盖 %s 笔）：%s",
        user_id,
        source,
        count,
        text[:80],
    )
    return {
        "question": text,
        "spec": spec,
        "source": source,
        "degraded": degraded,
        "message": message,
        "total": round2(total),
        "count": count,
        "grouped": [
            {
                "key": (
                    "(未填商户)"
                    if not r["key"] and spec["group_by"] == "merchant"
                    else str(r["key"])
                ),
                "amount": round2(r["total"]),
                "count": int(r["count"]),
            }
            for r in rows
        ],
        "truncated": truncated,
        "details": [
            {
                "id": d["id"],
                "tx_time": d["tx_time"],
                "account": d["account"],
                "tx_type": d["tx_type"],
                "merchant": d["merchant"],
                "category": d["category"],
                "amount": round2(d["amount"]),
            }
            for d in details
        ],
        "answer": _build_answer(spec, total, count),
    }
