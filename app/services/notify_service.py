"""通知中心业务逻辑（T-5.4）：事件开关、事件落库与出站 Webhook 推送

方案 B（docs/devlog 开发计划 T-5.4）：应用内通知中心 + 用户自配出站
Webhook（Bark / ntfy / 企业微信机器人 / 通用 JSON 接收端）。飞牛官方
「应用 → 设备」推送通道未确认存在，不作为交付前提（预研结论见需求清单
REQ-NTF-004）。

设计红线：
- 通知是旁路能力：本模块对外的所有入口都不得抛出异常打断主流程
  （调度器、目录导入、报告生成先干活，通知失败只记日志/落库）；
- 出站请求禁用重定向跟随、限制超时（与 AI 通道同策略，防 SSRF/挂死）；
- 事件按 event_key 唯一约束去重，同一事件不重复入库与推送。
"""

import json
import logging
import time
import urllib.error
import urllib.request
from urllib.parse import quote

from app.file_settings import (
    WEBHOOK_TIMEOUT,
    WEBHOOK_TYPES,
    NotifySettings,
    load_notify_settings,
    save_notify_settings,
)
from app.db.dao import notify_dao

logger = logging.getLogger(__name__)

# 事件类型注册表：key -> 展示名（设置页逐类开关的单一来源；
# 新增事件类型时前端文案由接口下发，无需另改前端枚举）
EVENT_TYPES: dict[str, str] = {
    "budget_exceeded": "预算超支",
    "budget_near_limit": "预算接近上限",
    "report_ready": "报告就绪",
    "import_done": "自动导入完成",
    "task_failed": "定时任务异常",
}

# 预算接近上限的判定比例（开发计划 T-5.4：80%）
BUDGET_NEAR_RATIO = 0.8


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止跟随重定向（与 ai_service / update_service 同策略）

    Webhook 地址由管理员配置，跟随 3xx 等于允许把出站请求（及其携带的通知
    内容）引向任意主机——内网地址可借跳转绕过 scheme 校验（SSRF）。
    必须显式重写 redirect_request 返回 None：仅实例化基类等于 urllib 默认
    行为（仍然跟随，安全审计实测）；返回 None 使 3xx 以 HTTPError 抛出、
    由 send_webhook 的既有分支转成 "HTTP 302" 可读失败原因。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT = _NoRedirect()


def event_enabled(event_type: str, settings: NotifySettings | None = None) -> bool:
    """事件开关：未配置过的类型默认开启（注册表新增类型时无需迁移配置）"""
    settings = settings or load_notify_settings()
    return bool(settings.resolved_events().get(event_type, True))


def get_config_view(is_admin: bool = True) -> dict:
    """前端配置视图：事件开关（含默认值补全）+ Webhook 概要（URL 不回传明文）

    url_hint 仅管理员可见（is_admin 口径与 ai.py 的 api_key_hint 一致）：
    bark/ntfy/wecom 的推送 Key 恰好都位于 URL 末尾，尾 6 位就是密钥片段，
    非管理员可见可用于针对性枚举/钓鱼——配置修改本就仅限管理员（PUT 走
    AdminUser），读侧掩码与写侧权限对齐。
    """
    settings = load_notify_settings()
    events = {key: True for key in EVENT_TYPES}
    events.update(settings.resolved_events())
    url = settings.webhook_url
    return {
        "events": [
            {"type": key, "label": label, "enabled": events[key]}
            for key, label in EVENT_TYPES.items()
        ],
        "webhook": {
            "enabled": settings.webhook_enabled,
            "type": settings.webhook_type,
            "has_url": bool(url),
            "url_hint": f"****{url[-6:]}" if url and is_admin else "",
            "types": list(WEBHOOK_TYPES),
        },
    }


def save_config_view(
    payload_events: dict[str, bool] | None, webhook: dict | None
) -> dict:
    """保存配置：事件开关按注册表白名单收敛；webhook_url 为 None 表示保持不变"""
    settings = load_notify_settings()
    if payload_events is not None:
        known = set(EVENT_TYPES)
        settings.events = {
            str(k): bool(v) for k, v in payload_events.items() if k in known
        }
    if webhook is not None:
        settings.webhook_enabled = bool(webhook.get("enabled", False))
        wtype = str(webhook.get("type") or settings.webhook_type)
        settings.webhook_type = wtype if wtype in WEBHOOK_TYPES else "generic"
        url = webhook.get("url")
        if url is not None:  # None = 保持不变（掩码回显下前端不回传原值）
            settings.webhook_url = str(url).strip()
    settings = settings.sanitized()
    save_notify_settings(settings)
    return get_config_view()


def create_event(
    event_type: str,
    title: str,
    content: str,
    *,
    user_id: str = "",
    dedup_key: str = "",
) -> dict | None:
    """产生一条事件通知：开关关闭/重复事件静默跳过；任何异常只记日志

    user_id 为空串 = 应用级广播；dedup_key 由生产方保证同一事件稳定相同
    （如 月份+分类），不同事件互异（如带时间戳）。返回落库的通知（未落库
    返回 None），供测试断言。
    """
    try:
        if not event_enabled(event_type):
            return None
        event_key = f"{event_type}:{dedup_key}" if dedup_key else event_type
        created = notify_dao.NotificationDAO.create(
            user_id=user_id,
            event_type=event_type,
            event_key=event_key,
            title=title,
            content=content,
        )
        if created is None:
            logger.info("通知事件重复，跳过：%s", event_key)
            return None
        _push_webhook(created)
        return created
    except Exception:  # 通知失败绝不影响任务/导入/报告主流程
        logger.exception("通知事件处理失败：%s", event_type)
        return None


def _push_webhook(notification: dict) -> None:
    """出站推送（best-effort）：未启用/未配置直接返回；结果落库供追溯"""
    try:
        settings = load_notify_settings()
        if not settings.webhook_enabled or not settings.webhook_url:
            return
        ok, error = send_webhook(
            settings.webhook_type,
            settings.webhook_url,
            notification["title"],
            notification["content"],
        )
        notify_dao.NotificationDAO.set_push_result(notification["id"], ok, error)
        if not ok:
            logger.warning("通知出站失败（%s）：%s", settings.webhook_type, error)
    except Exception:
        logger.exception("通知出站推送异常（不影响主流程）")


def send_webhook(wtype: str, url: str, title: str, content: str) -> tuple[bool, str]:
    """按渠道格式推送一条通知，返回 (是否成功, 失败原因)

    仅支持 http(s)、禁用重定向跟随、限制超时 —— 通知 URL 由管理员配置，
    与 AI 通道同等的 SSRF 防线（不跟随重定向防止内网地址借 3xx 绕过校验）。
    """
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return False, "Webhook 地址必须以 http(s):// 开头"
    try:
        request = _build_request(wtype, url, title, content)
        opener = urllib.request.build_opener(_NO_REDIRECT)
        with opener.open(request, timeout=WEBHOOK_TIMEOUT) as resp:
            status = resp.status
            payload = resp.read(1024).decode("utf-8", errors="replace")
        if 200 <= status < 300:
            # 企业微信/ntfy 等渠道 HTTP 200 也可能携带业务错误（errcode != 0）
            return _check_channel_payload(wtype, payload)
        return False, f"HTTP {status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        # 部分异常文案会内嵌完整 URL（Bark/ntfy 的地址里带推送 Key），而失败
        # 原因会落库、进运行日志并展示在通知中心，回传前把配置的 URL 抹掉
        if url and url in message:
            message = message.replace(url, "<webhook>")
        return False, message


def _build_request(
    wtype: str, url: str, title: str, content: str
) -> urllib.request.Request:
    """构造各渠道的请求对象（渠道格式差异收口在此）"""
    text = f"{title}\n{content}".strip()
    if wtype == "bark":
        # Bark 路径式推送：https://api.day.app/<key>/<title>/<body>
        full_url = f"{url.rstrip('/')}/{quote(title)}/{quote(content)}"
        request = urllib.request.Request(full_url, method="GET")
        return request
    if wtype == "ntfy":
        # ntfy 主题发布：标题并入正文，规避 Header 非 ASCII 编码问题
        request = urllib.request.Request(
            url,
            data=text.encode("utf-8"),
            method="POST",
            headers={"Content-Type": "text/plain; charset=utf-8"},
        )
        return request
    if wtype == "wecom":
        # 企业微信机器人：text 消息上限 2048 字节，超限截断防整条被拒
        body = json.dumps(
            {"msgtype": "text", "text": {"content": text[:1024]}},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        return request
    # generic：自建接收端，POST JSON {title, content}
    body = json.dumps({"title": title, "content": content}, ensure_ascii=False).encode(
        "utf-8"
    )
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    return request


def _check_channel_payload(wtype: str, payload: str) -> tuple[bool, str]:
    """渠道业务码校验：HTTP 200 不代表投递成功（wecom errcode / ntfy JSON 发布）"""
    if wtype != "wecom" or not payload:
        return True, ""
    try:
        data = json.loads(payload)
    except ValueError:
        return True, ""  # 非 JSON 响应不深究，交由 HTTP 状态判定
    errcode = data.get("errcode")
    if errcode in (None, 0):
        return True, ""
    return False, f"wecom errcode={errcode} {str(data.get('errmsg') or '')[:100]}"


def test_webhook(wtype: str, url: str) -> tuple[bool, str]:
    """发送测试推送（不落库、不写配置）：返回 (是否成功, 用户可读结果)"""
    if wtype not in WEBHOOK_TYPES:
        return False, "不支持的 Webhook 类型"
    ok, error = send_webhook(
        wtype, url, "fn-finstat 测试通知", "这是一条测试通知，收到即表示配置可用。"
    )
    return ok, ("发送成功" if ok else f"发送失败：{error}")


# ---- 事件生产者（各业务路径调用；全部吞异常，绝不影响调用方） ----


def notify_task_disabled(task_key: str, name: str, message: str) -> None:
    """定时任务连续失败被自动停用（REQ-AUT-004 的「通知事件」）"""
    create_event(
        "task_failed",
        f"定时任务「{name}」已自动停用",
        f"连续失败达到上限已自动停用，最近错误：{message}。请到 设置 → 自动化 排查后重新启用。",
        dedup_key=f"{task_key}:{time.time():.0f}",
    )


def notify_import_done(summary: str, owner_user_id: str = "") -> None:
    """目录监听自动导入完成（有新增流水时才调用）"""
    create_event(
        "import_done",
        "账单自动导入完成",
        summary,
        user_id=owner_user_id,
        dedup_key=f"scan:{time.time():.0f}",
    )


def notify_report_ready(user_id: str, title: str) -> None:
    """AI 报告生成/归档完成（生成耗时可达数十秒，用户可能已切走）"""
    create_event(
        "report_ready",
        "AI 报告已就绪",
        f"《{title}》已生成完成，可到 AI 报告面板查看。",
        user_id=user_id,
        dedup_key=f"{user_id}:{title}:{time.time():.0f}",
    )


def check_budget_events(user_id: str, month: str) -> None:
    """预算超支 / 接近上限（80%）检查：导入完成后对当月预算逐条判定

    去重键含月份与分类：同一（月，分类）每类提醒至多一次 —— 预算是月度
    口径，重复提醒只会造成打扰；提高预算后仍超支的场景下月重新提醒。
    """
    try:
        from app.services import budget_service  # 局部导入避免 services 间环

        overview = budget_service.overview(user_id, month)
    except Exception:
        logger.exception("预算通知检查失败（不影响调用方）")
        return
    for item in overview.get("items", []):
        label = item["category"] or "总预算"
        budget = float(item["budget"] or 0)
        expense = float(item["expense"] or 0)
        if budget <= 0 or expense <= 0:
            continue
        scope = f"{user_id}:{month}:{item['category'] or '_total'}"
        if expense > budget:
            create_event(
                "budget_exceeded",
                f"预算超支提醒（{label}）",
                f"{month} {label}已支出 {expense:.2f} 元，超出预算 {budget:.2f} 元。",
                user_id=user_id,
                dedup_key=f"{scope}:exceeded",
            )
        elif expense >= budget * BUDGET_NEAR_RATIO:
            create_event(
                "budget_near_limit",
                f"预算接近上限（{label}）",
                f"{month} {label}已支出 {expense:.2f} 元，达到预算 {budget:.2f} 元的 "
                f"{expense / budget * 100:.0f}%。",
                user_id=user_id,
                dedup_key=f"{scope}:near",
            )
