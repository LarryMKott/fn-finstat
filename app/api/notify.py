"""通知中心接口：应用内通知列表 / 未读角标 / 全部已读 + 设置页通知配置

通知列表与角标对所有登录用户开放（广播 + 定向到本人），「全部已读」只动
本人的水位线；通知配置为应用级共享（与 NAS/AI 配置同策略），读开放、
写仅管理员。
"""

from fastapi import APIRouter, Depends, Query

from app.api.deps import AdminUser, CurrentUser, request_db_session
from app.file_settings import load_notify_settings
from app.core.errors import ValidationError
from app.db.dao import notify_dao
from app.schemas.common import ApiResponse, ok
from app.schemas.notify import (
    NotificationList,
    NotificationOut,
    NotifyConfigIn,
    NotifyConfigView,
    ReadAllResult,
    UnreadCount,
    WebhookTestIn,
    WebhookTestResult,
)
from app.services import audit_service, notify_service

router = APIRouter(
    prefix="/api/notifications",
    tags=["通知"],
    dependencies=[Depends(request_db_session)],
)

config_router = APIRouter(
    prefix="/api/settings/notify",
    tags=["通知"],
    dependencies=[Depends(request_db_session)],
)


def _to_out(row: dict, watermark: int) -> NotificationOut:
    return NotificationOut(
        id=row["id"],
        user_id=row["user_id"],
        event_type=row["event_type"],
        title=row["title"],
        content=row["content"],
        push_status=row["push_status"],
        push_error=row["push_error"],
        created_at=row["created_at"],
        read=row["id"] <= watermark,
    )


@router.get(
    "",
    response_model=ApiResponse[NotificationList],
    summary="通知列表（广播 + 定向本人，含未读数）",
)
def list_notifications(
    user: CurrentUser, limit: int = Query(50, ge=1, le=100, description="返回条数上限")
):
    watermark = notify_dao.NotificationDAO.get_read_watermark(user.user_id)
    rows = notify_dao.NotificationDAO.list_for_user(user.user_id, limit)
    items = [_to_out(r, watermark) for r in rows]
    unread = sum(1 for i in items if not i.read)
    return ok(NotificationList(items=items, unread=unread))


@router.get(
    "/unread-count",
    response_model=ApiResponse[UnreadCount],
    summary="未读角标数（轮询用轻量接口）",
)
def unread_count(user: CurrentUser):
    return ok(UnreadCount(unread=notify_dao.NotificationDAO.unread_count(user.user_id)))


@router.post(
    "/read-all",
    response_model=ApiResponse[ReadAllResult],
    summary="全部已读（把本人已读水位线推到当前最大通知 id）",
)
def read_all(user: CurrentUser):
    last_id = notify_dao.NotificationDAO.mark_all_read(user.user_id)
    return ok(ReadAllResult(last_read_id=last_id))


@config_router.get(
    "/config",
    response_model=ApiResponse[NotifyConfigView],
    summary="通知配置视图（事件开关 + Webhook 掩码概要）",
)
def get_notify_config(user: CurrentUser):
    # url_hint 仅管理员可见（与 AI Key 掩码同口径；本地无网关头视为管理员）
    is_admin = not user.user_id or user.is_admin
    return ok(NotifyConfigView(**notify_service.get_config_view(is_admin)))


@config_router.put(
    "/config",
    response_model=ApiResponse[NotifyConfigView],
    summary="保存通知配置（事件开关按注册表白名单收敛）",
)
def save_notify_config(user: AdminUser, payload: NotifyConfigIn):
    view = notify_service.save_config_view(payload.events, payload.webhook)
    audit_service.record(
        user.user_id,
        "notify.config",
        "notify_config",
        None,
        "保存通知配置（webhook="
        + ("开" if payload.webhook and payload.webhook.get("enabled") else "关")
        + "）",
    )
    return ok(NotifyConfigView(**view))


@config_router.post(
    "/webhook-test",
    response_model=ApiResponse[WebhookTestResult],
    summary="发送测试推送（不落库；url 缺省时用已保存的地址测试）",
)
def test_webhook(_: AdminUser, payload: WebhookTestIn):
    url = payload.url.strip()
    wtype = payload.type
    if not url:
        # 界面对已保存地址只回显掩码：前端未重新输入时拿不到原值，
        # 此时整体回退到已保存配置（地址 + 类型）来测试
        settings = load_notify_settings()
        url, wtype = settings.webhook_url, settings.webhook_type
    if not url:
        raise ValidationError("请先填写 Webhook 地址")
    ok_sent, message = notify_service.test_webhook(wtype, url)
    return ok(WebhookTestResult(ok=ok_sent, message=message))
