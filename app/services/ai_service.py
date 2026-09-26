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
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from app.config import DEFAULT_CATEGORY
from app.file_settings import AISettings, ai_provider_label, load_ai_settings
from app.core.constants import TX_TYPE_LABELS
from app.core.errors import BizError, ErrorCode, NotFoundError, ValidationError
from app.db.dao.ai_report_dao import AIReportDAO
from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from app.db.dao.category_keyword_dao import CategoryKeywordDAO
from app.db.dao.stat_dao import StatDAO
from app.schemas.ai import AITestResult
from app.schemas.category import CATEGORY_NAME_MAX_LENGTH
from app.services import audit_service, notify_service, keyword_service
from app.utils.amount import round2 as _round2
from app.utils.net_guard import OutboundBlockedError, validate_outbound_url
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

# 关键词回填（auto_keyword_enabled，批内变体）的量级护栏
KEYWORD_BACKFILL_CATEGORIES = 5  # 单次归类请求最多回填的分类数
KEYWORD_BACKFILL_SAMPLE = 20  # 每个分类最多送入的商户样本数
KEYWORD_BACKFILL_PER_CATEGORY = 10  # 每个分类最多采纳的关键词数

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

# CAP-3（auto_category_enabled）放开了白名单约束后的 system prompt：
# 新分类名必须短（后续建分类截断到 CATEGORY_NAME_MAX_LENGTH，且界面观感优先）、
# 同类消费提名保持一致——一致性是「≥2 笔提名才建分类」护栏能生效的前提
_SYSTEM_PROMPT_CREATE = (
    "你是个人记账分类助手。用户会给出若干笔交易（编号、商户、备注、类型、金额）"
    "和候选消费分类，请为每笔交易从候选分类中选出最合适的一个。"
    "若某笔交易确实不属于任何候选分类，可以提出一个新分类名：简体中文、"
    "不超过 6 个字、不与候选分类重名；同一类消费必须使用同一个新分类名。"
    '输出 JSON 对象：{"result": {"<编号>": "<分类名或新分类名>"}}，'
    "编号必须与输入一致，每笔交易都必须给出分类。"
)

# auto_subcategory_enabled（需同时开启 auto_category_enabled）附加的层级表达：
# 「父分类名/新分类名」斜杠协议——解析层拆开，父名必须是候选分类才采纳
_SYSTEM_PROMPT_SUBCATEGORY = (
    "提出的新分类若明显从属于某个候选分类，用「候选分类名/新分类名」格式表达层级，"
    "否则直接输出新分类名。"
)


def _chat(
    settings: AISettings,
    messages: list[dict],
    max_tokens: int,
    timeout: float = REQUEST_TIMEOUT,
) -> str:
    """调用 chat/completions 返回文本内容；网络/协议错误统一抛 AIClientError

    出站前做一次目标地址安全校验（纵深防御）：保存配置时 `api/ai.py` 已校验过，
    但配置文件可被直接篡改、也可能残留旧版本的越界地址，故在真正发包前再拦一道，
    确保 API Key 不会以 Bearer 头发往内网/回环/云元数据地址。
    """
    try:
        validate_outbound_url(settings.base_url)
    except OutboundBlockedError as exc:
        raise AIClientError(f"AI 服务地址不安全，已拒绝调用：{exc}") from exc

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


def _parse_json_object(content: str) -> dict:
    """解析模型返回的 JSON 对象，兼容 ``` 围栏包裹

    空内容单独报错：content 为空串/纯空白时（推理类模型把输出放进
    reasoning_content、内容被过滤、端点不支持 response_format=json_object
    都会这样），沿用的「不是有效 JSON」报错会把 content[:120] 留成空串，
    日志只剩一个冒号，排查时无从下手。
    """
    if not str(content).strip():
        raise AIClientError(
            "AI 返回了空内容（常见原因：模型为推理型只输出 reasoning_content、"
            "端点不支持 JSON 输出模式、内容被安全过滤），请检查模型配置后重试"
        )
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # 模型偶发在 JSON 外包裹 ``` 围栏，剥掉后重试一次
        try:
            data = json.loads(strip_code_fence(content))
        except json.JSONDecodeError as exc:
            raise AIClientError(f"AI 返回内容不是有效 JSON：{content[:120]}") from exc
    if not isinstance(data, dict):
        raise AIClientError("AI 返回 JSON 缺少 result 字段")
    return data


def _split_name_candidate(raw: str, allowed: set[str]) -> tuple[str, str | None] | None:
    """把模型输出的分类名整理为候选元组（名字, 父分类名或 None）

    - 白名单内的名字直接返回（走常规 assignment，不进候选）
    - 「父/子」斜杠名仅在父名确实是候选分类时拆开；父名无效按整体新名处理
    - 返回 None 表示无法作为新分类候选（空、超长截断后为空等）
    """
    raw = str(raw).strip()
    if not raw:
        return None
    if "/" in raw:
        parent, _, child = raw.partition("/")
        parent = parent.strip()
        child = child.strip()[:CATEGORY_NAME_MAX_LENGTH]
        if not child:
            return None
        return (child, parent if parent in allowed else None)
    return (raw[:CATEGORY_NAME_MAX_LENGTH], None)


# 抢救提取：result 载荷形状固定为 "编号": "分类名"，严解析失败（max_tokens
# 截断 / 前后夹带说明文字 / 轻度格式破洞）时按该形状做确定性抢救。键值内
# 不允许引号与反斜杠转义（分类名是短中文词，命中即可信）；截断在键值中途的
# 残行因缺收尾引号不会被匹配，天然只捞完整对
_RESULT_PAIR_RE = re.compile(r'"(\d+)"\s*:\s*"([^"\\]{1,64})"')


def _parse_result(
    content: str, total: int, allowed: set[str], allow_create: bool = False
) -> tuple[dict[int, str], dict[int, tuple[str, str | None]]]:
    """解析归类结果：白名单内 → assignments；白名单外且允许建新类 → candidates

    candidates 值为 (新分类名, 父分类名或 None)，是否真的建分类由
    _promote_new_categories 按提名数与配额裁决（不信任模型的单次输出）。

    严解析失败（JSON 截断 / result 键缺失 / 夹带文字）时走一次零成本抢救：
    按已知载荷形状提取完整键值对，逐条套用与严解析完全相同的校验（编号
    越界、白名单、候选清洗），一条都捞不回才抛错——一批最多 50 条流水，
    能捞回多少是多少，避免整个批次白白保留原分类。
    """
    try:
        mapping = _parse_json_object(content).get("result")
        if not isinstance(mapping, dict):
            raise AIClientError("AI 返回 JSON 缺少 result 字段")
    except AIClientError:
        salvaged, salvaged_candidates = _salvage_result_pairs(
            content, total, allowed, allow_create
        )
        if salvaged or salvaged_candidates:
            logger.warning(
                "AI 返回 JSON 解析失败，已按形状抢救 %s 条归类（其中 %s 条新类目候选）",
                len(salvaged),
                len(salvaged_candidates),
            )
            return salvaged, salvaged_candidates
        raise
    result: dict[int, str] = {}
    candidates: dict[int, tuple[str, str | None]] = {}
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
        elif allow_create:
            candidate = _split_name_candidate(name, allowed)
            if candidate is not None:
                candidates[idx] = candidate
    return result, candidates


def _salvage_result_pairs(
    content: str, total: int, allowed: set[str], allow_create: bool
) -> tuple[dict[int, str], dict[int, tuple[str, str | None]]]:
    """从解析失败的返回文本抢救 "编号": "分类名" 对（校验口径与严解析一致）"""
    result: dict[int, str] = {}
    candidates: dict[int, tuple[str, str | None]] = {}
    for idx_str, name in _RESULT_PAIR_RE.findall(str(content)):
        try:
            idx = int(idx_str)
        except ValueError:  # pragma: no cover - 正则已保证数字
            continue
        if not 0 <= idx < total:
            continue
        name = name.strip()
        if name in allowed:
            result[idx] = name
        elif allow_create:
            candidate = _split_name_candidate(name, allowed)
            if candidate is not None:
                candidates[idx] = candidate
    return result, candidates


def _parse_assignments(content: str, total: int, allowed: set[str]) -> dict[int, str]:
    """解析 AI 返回的 JSON，仅保留编号合法且分类在候选列表内的结果

    兼容旧签名（allow_create=False 口径）；CAP-3 分支走 _parse_result。
    """
    result, _candidates = _parse_result(content, total, allowed, allow_create=False)
    return result


# ---- CAP-3：归类时自动建分类（默认关闭，auto_category_enabled 放开）----

# 抗单次幻觉：同一批中被 >= 该笔数的流水共同提名的新类目才创建
AUTO_CREATE_MIN_NOMINATIONS = 2
# 配额护栏：单批最多创建的新分类数（防模型批量发明分类把界面搞烂）
AUTO_CREATE_MAX_PER_BATCH = 2


def _promote_new_categories(
    candidates: dict[int, tuple[str, str | None]],
    settings: AISettings,
    user_id: str = "",
) -> dict[int, str]:
    """把获得足够提名的白名单外类目建为真分类，返回 {记录下标: 已建分类名}

    护栏（设计 §6.3 / §11）：同批 ≥2 笔提名才建（抗单次幻觉）、单批最多
    2 个（配额）、名字在建分类前截断到 CATEGORY_NAME_MAX_LENGTH、
    source='ai' 可溯源、写入审计 category.auto_create。
    层级：仅当 auto_subcategory_enabled 且父名确实是现有顶层分类时挂为子类，
    否则一律建为顶层（不信任模型对层级的口头承诺）。
    """
    votes: dict[str, int] = {}
    parents: dict[str, str | None] = {}
    for name, parent in candidates.values():
        votes[name] = votes.get(name, 0) + 1
        parents.setdefault(name, parent)
    qualified = sorted(
        (name for name, count in votes.items() if count >= AUTO_CREATE_MIN_NOMINATIONS),
        key=lambda name: (-votes[name], name),
    )
    created: dict[str, str] = {}
    for name in qualified[:AUTO_CREATE_MAX_PER_BATCH]:
        parent_id = None
        parent_name = parents.get(name)
        if parent_name and settings.auto_subcategory_enabled:
            parent_cat = CategoryDAO.get_by_name(parent_name)
            if parent_cat is not None and parent_cat["parent_id"] is None:
                parent_id = parent_cat["id"]
        new_id = CategoryDAO.create(name, parent_id=parent_id, source="ai")
        if new_id is None:
            continue  # 并发下同名分类已存在：该提名按落空处理，不阻断归类
        created[name] = name
        audit_service.record(
            user_id,
            "category.auto_create",
            "category",
            new_id,
            f"AI 归类自动创建分类「{name}」（{votes[name]} 笔流水提名"
            + ("，挂在「" + parent_name + "」下）" if parent_id else "")
            + "）",
        )
    return {idx: name for idx, (name, _parent) in candidates.items() if name in created}


def classify_records(
    records: list[dict],
    categories: list[str],
    settings: AISettings,
    allow_create: bool = False,
) -> dict[int, str]:
    """把一批流水交给 AI 归类，返回 {记录下标: 分类}；失败抛 AIClientError

    allow_create（CAP-3）为 False 时与历史行为完全一致：白名单外结果一律
    丢弃。为 True 时白名单外提名进入配额裁决，达标者现场建分类并纳入结果。
    """
    # 去重并保持传入顺序（分类表 id 序），保证 prompt 稳定可读
    ordered: list[str] = []
    for c in categories:
        if c and c not in ordered:
            ordered.append(c)
    if not records or not ordered or not settings.ready:
        return {}
    system_prompt = _SYSTEM_PROMPT
    if allow_create:
        system_prompt = _SYSTEM_PROMPT_CREATE
        if settings.auto_subcategory_enabled:
            system_prompt += _SYSTEM_PROMPT_SUBCATEGORY
    content = _chat(
        settings,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _build_user_prompt(records, ordered)},
        ],
        max_tokens=min(4000, 64 * len(records) + 200),
    )
    assignments, candidates = _parse_result(
        content, len(records), set(ordered), allow_create=allow_create
    )
    if candidates:
        assignments.update(_promote_new_categories(candidates, settings))
    return assignments


def classify_batches(
    records: list[dict],
    categories: list[str],
    settings: AISettings,
    time_budget: float | None = None,
    allow_create: bool = False,
) -> tuple[dict[int, str], bool]:
    """分批归类（按批容错），time_budget 秒用尽即停

    返回 ({记录下标: 分类}, 是否处理完全部批次)；时间预算用尽时 completed=False。
    allow_create 透传给每批（CAP-3 配额按批生效：单批最多 2 个新分类）。
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
            batch_result = classify_records(
                batch, categories, settings, allow_create=allow_create
            )
            assignments.update({start + idx: cat for idx, cat in batch_result.items()})
        except AIClientError as exc:
            logger.warning("AI 归类单批失败（该批保留原分类）：%s", exc)
    return assignments, True


def _auto_backfill_keywords(
    settings: AISettings,
    records: list[dict],
    assignments: dict[int, str],
) -> None:
    """auto_keyword_enabled：归类请求顺带做一次高频关键词回填（T-A 批内变体）

    只送商户名与其刚被归入的分类名（无金额/日期/备注，隐私红线与归类一致）；
    单次归类请求最多回填一次、最多 KEYWORD_BACKFILL_CATEGORIES 个分类、每类
    KEYWORD_BACKFILL_PER_CATEGORY 个词，全部落 source='ai'。任何失败只记
    日志，绝不影响归类结果。
    """
    if not settings.auto_keyword_enabled or not assignments:
        return
    by_category: dict[str, list[str]] = {}
    for idx, category in assignments.items():
        merchant = str(records[idx].get("merchant") or "").strip()
        if merchant:
            by_category.setdefault(category, []).append(merchant)
    involved = sorted(by_category)[:KEYWORD_BACKFILL_CATEGORIES]
    if not involved:
        return
    sample = {
        category: list(dict.fromkeys(by_category[category]))[:KEYWORD_BACKFILL_SAMPLE]
        for category in involved
    }
    lines = [
        "请为以下分类总结可代表它的高频关键词（用于商户名子串匹配自动归类）。"
        "每个分类最多 10 个词，每个词 2-8 个字、不含空格；只总结样本中反复"
        "出现的品牌/商户类型词，宁缺毋滥。",
        "",
    ]
    for category, merchants in sample.items():
        lines.append(f"分类「{category}」商户样本：{'、'.join(merchants)}")
    try:
        content = _chat(
            settings,
            [
                {
                    "role": "system",
                    "content": "你是记账分类关键词助手，只输出 JSON。",
                },
                {"role": "user", "content": "\n".join(lines)},
            ],
            max_tokens=1500,
        )
        data = _parse_json_object(content).get("keywords")
    except AIClientError as exc:
        logger.warning("AI 关键词回填失败（已忽略）：%s", exc)
        return
    if not isinstance(data, dict):
        return
    total = 0
    for category, words in data.items():
        if category not in involved or not isinstance(words, list):
            continue
        valid, _dropped = keyword_service.clean_keywords(
            words[:KEYWORD_BACKFILL_PER_CATEGORY]
        )
        if not valid:
            continue
        try:
            cat = CategoryDAO.get_by_name(category)
            if cat is None:
                continue  # 提名分类已被并发删除：跳过
            result = keyword_service.add_keywords(cat["id"], valid, source="ai")
        except Exception as exc:  # 回填是旁路能力：单分类失败不影响其余
            logger.warning("AI 关键词回填「%s」失败（已忽略）：%s", category, exc)
            continue
        total += result["added"]
    if total:
        logger.info("AI 归类顺带回填关键词 %s 个（%s 个分类）", total, len(involved))


def enhance_import_records(records: list[dict]) -> int:
    """导入时对关键词未命中（"其他"）的记录做 AI 二次归类，返回改写分类的条数

    未启用 / 未配置密钥直接跳过；任何异常都不影响导入主流程。
    原地改写 records 中各记录的 category（仅限候选分类，AI 结果已过白名单校验；
    auto_category_enabled 开启时白名单外的达标提名可现场建新分类）。
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
            allow_create=settings.auto_category_enabled,
        )
    except Exception as exc:  # 兜底：分类失败绝不阻断导入
        logger.warning("导入时 AI 归类失败：%s", exc)
        return 0
    logger.info(
        "导入智能归类：%s 条关键词未命中，AI 改写 %s 条", len(pending), len(assignments)
    )
    for local_idx, category in assignments.items():
        records[pending[local_idx]]["category"] = category
    sent = [records[i] for i in pending]
    try:
        _auto_backfill_keywords(settings, sent, assignments)
    except Exception as exc:  # 回填绝不阻断导入
        logger.warning("导入时 AI 关键词回填失败：%s", exc)
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
        records,
        categories,
        settings,
        time_budget=CLASSIFY_TIME_BUDGET,
        allow_create=settings.auto_category_enabled,
    )
    try:
        _auto_backfill_keywords(settings, records, assignments)
    except Exception as exc:  # 回填绝不阻断归类主流程
        logger.warning("智能分类时 AI 关键词回填失败：%s", exc)
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


# ---- 分类扩展 AI 任务（v1.1）：T-A 关键词生成 / T-B 子类方案（两段式）----
# 两段式（generate 预览不落库 → 人工勾选 → apply 落库）是成本与质量双闸门：
# 生成一次要花 token，用户可能只采纳其中一部分词。


_KEYWORD_SAMPLE_LIMIT = 30  # T-A 送入的商户样本上限（设计 §6.1 Top 30）
_CHILDREN_SAMPLE_LIMIT = 50  # T-B 送入的商户样本上限（设计 §6.2 Top 50）
_CHILDREN_MAX_COUNT = 5  # 子类数量硬上限（超限截断，不信任模型自律）
_CHILD_NAME_PROMPT_LIMIT = 6  # 子类名长度 prompt 约束（服务端硬上限是列宽 20）
_CHILD_KEYWORD_MIN = 5  # 每个子类关键词数的 prompt 约束（服务端只剔除空词组）
_KEYWORD_SYSTEM_PROMPT = (
    "你是记账分类关键词助手。给定一个消费分类和它下面的高频商户样本，"
    "请总结出能代表该分类的关键词列表，用于按「子串包含」匹配商户名自动归类。"
    "要求：30-40 个关键词；每个 2-8 个字、不含空格和标点；覆盖样本中的品牌名、"
    "商户类型词与通用词；品牌词保留通用主体（如「美团外卖」给「美团」即可，"
    "门店后缀去掉）。"
    '输出 JSON 对象：{"keywords": ["词1", "词2", ...]}'
)

_CHILDREN_SYSTEM_PROMPT = (
    "你是记账分类体系设计助手。给定一个消费分类、它下面的高频商户样本与流水条数，"
    "判断是否值得细分子类，并给出子类方案。"
    "要求：子类不超过 5 个；每个子类名称不超过 "
    + str(_CHILD_NAME_PROMPT_LIMIT)
    + " 个字；每个子类给出 "
    + str(_CHILD_KEYWORD_MIN)
    + "-15 个关键词（用于子串匹配商户名，口径同关键词规则：2-8 个字、无空格）；"
    "reason 用一句话说明依据；已有分类列表里的名字不得再用作子类名；"
    "若样本不足以支撑有意义的细分，输出空数组，不要硬凑。"
    '输出 JSON 对象：{"children": [{"name": "子类名", "keywords": ["..."], '
    '"reason": "..."}]}'
)


def _require_ready() -> AISettings:
    """生成类任务的公共前置：已配置密钥，否则抛 AIClientError（路由转 400）"""
    settings = load_ai_settings()
    if not settings.ready:
        raise AIClientError(
            "尚未配置 AI API Key，请先在设置页填写",
            code=ErrorCode.AI_NOT_CONFIGURED,
        )
    return settings


def generate_keyword_candidates(category_id: int, hint: str | None = None) -> dict:
    """T-A：为分类生成关键词候选（预览，不落库），返回候选词 + 冲突标记

    清洗口径（设计 §6.1）：剔除非法词；与该分类已有词重复的静默去重；
    已属于**其它**分类的启用词保留但标记 conflict（是否采纳由人工裁决——
    同词跨分类合法，入库唯一键按 (category_id, keyword) 约束）。
    """
    settings = _require_ready()
    cat = CategoryDAO.get_by_id(category_id)
    if cat is None:
        raise NotFoundError("分类不存在")
    merchants = BillDAO.merchants_by_category(cat["name"], limit=_KEYWORD_SAMPLE_LIMIT)
    if not merchants:
        raise AIClientError(
            f"分类「{cat['name']}」下暂无流水样本，先导入该分类的账单再生成关键词"
        )
    lines = [f"分类名称：{cat['name']}", "高频商户样本："]
    lines += [f"- {m['merchant']}（{m['count']} 笔）" for m in merchants]
    if hint:
        lines += ["", f"用户补充说明：{hint.strip()[:200]}"]
    content = _chat(
        settings,
        [
            {"role": "system", "content": _KEYWORD_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(lines)},
        ],
        max_tokens=2000,
    )
    raw = _parse_json_object(content).get("keywords")
    if not isinstance(raw, list):
        raise AIClientError("AI 返回 JSON 缺少 keywords 字段")
    valid, dropped = keyword_service.clean_keywords(raw)
    # 冲突标记：词已被其它启用关键词占用时的归属分类（同分类已有词静默去重）
    self_words = {
        row["keyword"].lower()
        for row in CategoryKeywordDAO.list_by_category(category_id)
    }
    owner: dict[str, str] = {}
    for row in CategoryKeywordDAO.list_enabled_with_category():
        owner.setdefault(row["keyword"].lower(), row["category"])
    candidates = []
    for word in valid:
        if word.lower() in self_words:
            continue
        conflict = owner.get(word.lower())
        candidates.append(
            {
                "keyword": word,
                "conflict": conflict if conflict and conflict != cat["name"] else None,
            }
        )
    logger.debug(
        "T-A 关键词生成：分类=%s 样本=%s 候选=%s 剔除=%s",
        cat["name"],
        len(merchants),
        len(candidates),
        dropped,
    )
    return {
        "category": cat,
        "sample_size": len(merchants),
        "candidates": candidates,
        "dropped": dropped,
    }


def apply_keywords(category_id: int, keywords: list[str]) -> dict:
    """T-A 落库步：把人工勾选的候选词写入关键词表（source='ai'）"""
    return keyword_service.add_keywords(category_id, keywords, source="ai")


def _clean_children(raw: list, existing_names: set[str], self_name: str) -> list[dict]:
    """T-B 返回结果的二次过滤（不信任模型的结构承诺）

    口径（设计 §6.2）：名字截断到列宽；与已有分类/兄弟子类/父分类重名的
    整组剔除；关键词过服务层清洗，洗完全空的整组剔除；数量上限在清洗后
    生效（先截断会把坏行计入配额，挤掉后面的合格子类）。
    """
    seen = set(existing_names)
    seen.add(self_name)
    out: list[dict] = []
    for child in list(raw or []):
        if not isinstance(child, dict):
            continue
        name = str(child.get("name") or "").strip()[:CATEGORY_NAME_MAX_LENGTH]
        if not name or name in seen:
            continue
        reason = str(child.get("reason") or "").strip()[:200]
        keywords, _dropped = keyword_service.clean_keywords(
            child.get("keywords") if isinstance(child.get("keywords"), list) else []
        )
        if not keywords:
            continue
        seen.add(name)
        out.append({"name": name, "keywords": keywords, "reason": reason})
        if len(out) >= _CHILDREN_MAX_COUNT:
            break
    return out


def generate_subcategory_plan(category_id: int) -> dict:
    """T-B：子类方案预览（不落库），返回清洗后的子类候选（含每子类关键词）"""
    settings = _require_ready()
    cat = CategoryDAO.get_by_id(category_id)
    if cat is None:
        raise NotFoundError("分类不存在")
    if cat["parent_id"] is not None:
        raise ValidationError("仅顶层分类支持细分子类")
    merchants = BillDAO.merchants_by_category(cat["name"], limit=_CHILDREN_SAMPLE_LIMIT)
    if not merchants:
        raise AIClientError(
            f"分类「{cat['name']}」下暂无流水样本，先导入账单再生成子类方案"
        )
    bill_count = BillDAO.count_by_category(cat["name"])  # 只给条数，不给金额
    lines = [
        f"分类名称：{cat['name']}",
        f"该分类流水条数：{bill_count}",
        "高频商户样本：",
    ]
    lines += [f"- {m['merchant']}（{m['count']} 笔）" for m in merchants]
    existing = [c["name"] for c in CategoryDAO.list_all()]
    lines += ["", "已有分类（子类不得重名）：" + "、".join(existing)]
    content = _chat(
        settings,
        [
            {"role": "system", "content": _CHILDREN_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(lines)},
        ],
        max_tokens=2500,
    )
    raw = _parse_json_object(content).get("children")
    if not isinstance(raw, list):
        raise AIClientError("AI 返回 JSON 缺少 children 字段")
    children = _clean_children(raw, set(existing), cat["name"])
    logger.debug(
        "T-B 子类生成：分类=%s 样本=%s 方案=%s", cat["name"], len(merchants), children
    )
    return {
        "category": cat,
        "bill_count": bill_count,
        "sample_size": len(merchants),
        "children": children,
    }


def apply_subcategories(
    category_id: int, children: list[dict], migrate_bills: bool = False
) -> dict:
    """T-B 落库步：创建子分类 + 落关键词；migrate_bills 时迁移命中的流水

    逐子类独立处理：重名/非法的子类跳过并在结果中说明，不整批失败。
    迁移按「父分类下命中的流水迁到对应子类」执行（全账号，分类是全局配置），
    未命中任何子类关键词的流水原地保留——D-1 口径：是否迁移由用户显式触发。
    """
    cat = CategoryDAO.get_by_id(category_id)
    if cat is None:
        raise NotFoundError("分类不存在")
    if cat["parent_id"] is not None:
        raise ValidationError("仅顶层分类支持细分子类")
    if not children:
        raise ValidationError("没有可应用的子类")
    # 二次过滤与生成侧同口径（apply 请求体同样不可信任）
    existing = {c["name"] for c in CategoryDAO.list_all()}
    cleaned = _clean_children(children, existing - {cat["name"]}, cat["name"])
    if not cleaned:
        raise ValidationError("没有可应用的子类（全部与已有分类重名或不合法）")
    created: list[dict] = []
    skipped: list[str] = []
    for child in cleaned:
        child_id = CategoryDAO.create(child["name"], parent_id=category_id, source="ai")
        if child_id is None:
            skipped.append(child["name"])  # 并发下重名：幂等跳过
            continue
        result = CategoryKeywordDAO.create_many(
            child_id, child["keywords"], source="ai"
        )
        created.append(
            {
                "id": child_id,
                "name": child["name"],
                "keywords": result,
                "reason": child["reason"],
            }
        )
    migrated = {"scanned": 0, "migrated": 0}
    if migrate_bills and created:
        migrated = BillDAO.migrate_to_subcategories(
            cat["name"],
            [{"name": c["name"], "keywords": child["keywords"]} for c in created],
        )
    return {
        "created": created,
        "skipped": skipped,
        "migrated": migrated["migrated"],
        "scanned": migrated["scanned"],
    }
