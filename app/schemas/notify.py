"""通知中心接口模型（应用内通知 + 设置页通知配置）"""

from pydantic import BaseModel


class NotificationOut(BaseModel):
    """通知列表条目（push_error 已截断，不含敏感信息）"""

    id: int
    user_id: str = ""
    event_type: str
    title: str
    content: str
    push_status: str = ""
    push_error: str = ""
    created_at: float
    read: bool = False


class NotificationList(BaseModel):
    """通知列表（含未读数，列表页与角标一次取全）"""

    items: list[NotificationOut]
    unread: int


class UnreadCount(BaseModel):
    unread: int


class ReadAllResult(BaseModel):
    """「全部已读」返回的新水位线"""

    last_read_id: int


class NotifyEventSwitch(BaseModel):
    """单类事件开关（下发含默认值与展示名）"""

    type: str
    label: str
    enabled: bool


class NotifyWebhookView(BaseModel):
    """Webhook 配置概要：URL 不回传明文，只给掩码提示"""

    enabled: bool
    type: str
    has_url: bool
    url_hint: str = ""
    types: list[str]


class NotifyConfigView(BaseModel):
    """设置页「通知」分区配置视图"""

    events: list[NotifyEventSwitch]
    webhook: NotifyWebhookView


class NotifyConfigIn(BaseModel):
    """保存通知配置：events/webhook 均可缺省（只改其一）；
    webhook.url 为 None 表示保持不变（掩码回显下前端不回传原值），空串表示清除"""

    events: dict[str, bool] | None = None
    webhook: dict | None = None


class WebhookTestIn(BaseModel):
    """测试出站推送：使用表单当前值发送，不落库不写配置

    url 为空串时回退用已保存的 Webhook 地址测试（界面只回显掩码，
    用户未重新输入时前端拿不到原值）。
    """

    type: str = "generic"
    url: str = ""


class WebhookTestResult(BaseModel):
    ok: bool
    message: str
