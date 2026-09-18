"""运行期文件配置：设置页/导入页在运行期读写的落盘 JSON 配置（AI / NAS 目录 / 通知）

与 app/config.py 的分工：
    config.py     启动时从环境变量/向导定型、进程内只读（HOST、PORT、数据库解析等）
    本模块        应用运行期间由用户在界面上改、写入 DATA_DIR 下的 JSON 文件，
                  写入后立即生效、无需重启（读取方每次操作现读，不缓存）

每个配置统一「四件套」模式，新增一套照抄即可：
    1. 文件路径常量（DATA_DIR / xxx_config.json）
    2. 配置模型（dataclass，含默认值；敏感信息与 ai_config.json 同策略明文存本地，
       v1.0 T-1.7 统一加密迁移）
    3. load_xxx_settings()：经 config.read_json_config 读取（缺失/损坏静默降级）
    4. save_xxx_settings()：经 config.write_json_config 原子写（并发保存不互相覆盖）
"""

import os
from dataclasses import dataclass

from app.config import DATA_DIR, read_json_config, write_json_config

# ==================================================================
# 1. AI 智能分类（DeepSeek）：设置页「智能分类」卡片读写 ai_config.json
# ==================================================================

AI_CONFIG_FILE = DATA_DIR / "ai_config.json"

AI_DEFAULT_BASE_URL = "https://api.deepseek.com"
AI_DEFAULT_MODEL = "deepseek-chat"


@dataclass
class AISettings:
    """DeepSeek 智能分类配置（应用级共享，不按账号区分；与 db_config.json 同策略明文存本地）"""

    api_key: str = ""
    base_url: str = AI_DEFAULT_BASE_URL
    model: str = AI_DEFAULT_MODEL
    enabled: bool = False  # 导入账单时自动调用 DeepSeek 二次归类

    @property
    def ready(self) -> bool:
        """已配置密钥即可发起调用；enabled 仅控制导入时的自动归类"""
        return bool(self.api_key.strip())


def load_ai_settings() -> AISettings:
    """读取 AI 配置；配置文件不存在时回退通用环境变量（本地开发可用 .env.dev 注入）"""
    data = read_json_config(
        AI_CONFIG_FILE,
        "AI",
        fallback={"api_key": os.environ.get("DEEPSEEK_API_KEY", "")},
    )
    return AISettings(
        api_key=str(data.get("api_key") or ""),
        base_url=str(data.get("base_url") or "").strip() or AI_DEFAULT_BASE_URL,
        model=str(data.get("model") or "").strip() or AI_DEFAULT_MODEL,
        enabled=bool(data.get("enabled", False)),
    )


def save_ai_settings(settings: AISettings) -> None:
    """设置页保存 AI 配置（写入文件后即生效，无需重启）"""
    write_json_config(
        AI_CONFIG_FILE,
        {
            "api_key": settings.api_key,
            "base_url": settings.base_url,
            "model": settings.model,
            "enabled": settings.enabled,
        },
    )


# ==================================================================
# 2. NAS 目录导入：导入页读写 nas_config.json
# ==================================================================

NAS_CONFIG_FILE = DATA_DIR / "nas_config.json"


@dataclass
class NASImportSettings:
    """NAS 目录导入配置（应用级共享；与 ai_config.json 同策略明文存本地）

    import_dir 为账单存放目录的绝对路径（如 fnOS 的 /vol1/1000/bills 或
    Windows 的 D:/bills），允许不存在（保存时不强制，浏览时提示）。
    """

    import_dir: str = ""
    # 自动导入归属的账号（配置者的飞牛 user_id；本地模式为空串），
    # 目录监听定时导入的流水归入该账号
    owner_user_id: str = ""


def load_nas_settings() -> NASImportSettings:
    """读取 NAS 导入配置；配置文件缺失/损坏时回退默认（未配置目录）"""
    data = read_json_config(NAS_CONFIG_FILE, "NAS 导入")
    return NASImportSettings(
        import_dir=str(data.get("import_dir") or "").strip(),
        owner_user_id=str(data.get("owner_user_id") or "").strip(),
    )


def save_nas_settings(settings: NASImportSettings) -> None:
    """导入页保存 NAS 目录配置（写入文件后即生效，无需重启）"""
    write_json_config(
        NAS_CONFIG_FILE,
        {
            "import_dir": settings.import_dir,
            "owner_user_id": settings.owner_user_id,
        },
    )


# ==================================================================
# 3. 通知中心（T-5.4，方案 B：应用内通知 + 用户自配出站 Webhook）：
#    设置页「通知」卡片读写 notify_config.json
# ==================================================================

NOTIFY_CONFIG_FILE = DATA_DIR / "notify_config.json"

# 通知事件的 Webhook 出站渠道类型（fmt 为各渠道的消息格式，非推送协议差异）
WEBHOOK_TYPES = ("bark", "ntfy", "wecom", "generic")
WEBHOOK_TIMEOUT = 10  # 出站超时（秒）：通知是旁路能力，不能拖住任务线程


@dataclass
class NotifySettings:
    """通知配置（应用级共享）：逐类事件开关 + 出站 Webhook

    webhook.url 按类型填：bark=https://api.day.app/<key>、
    ntfy=https://<服务器>/<主题>、wecom=企业微信机器人完整地址、
    generic=自建接收端完整地址（POST JSON {title, content}）。
    与 ai_config.json 同策略明文存本地（v1.0 T-1.7 统一加密迁移）。
    """

    # 逐类事件开关（键 = notify_service 的事件类型，默认全开）
    events: dict[str, bool] | None = None
    webhook_enabled: bool = False
    webhook_type: str = "generic"
    webhook_url: str = ""

    def resolved_events(self) -> dict[str, bool]:
        return dict(self.events or {})

    def sanitized(self) -> "NotifySettings":
        webhook_type = (
            self.webhook_type if self.webhook_type in WEBHOOK_TYPES else "generic"
        )
        return NotifySettings(
            events=dict(self.events or {}),
            webhook_enabled=bool(self.webhook_enabled and self.webhook_url.strip()),
            webhook_type=webhook_type,
            webhook_url=self.webhook_url.strip(),
        )


def load_notify_settings() -> NotifySettings:
    """读取通知配置；文件缺失/损坏时回退默认（事件全开、Webhook 关闭）"""
    data = read_json_config(NOTIFY_CONFIG_FILE, "通知")
    webhook = data.get("webhook") or {}
    if not isinstance(webhook, dict):
        webhook = {}
    events = data.get("events") or {}
    if not isinstance(events, dict):
        events = {}
    return NotifySettings(
        events={str(k): bool(v) for k, v in events.items()},
        webhook_enabled=bool(webhook.get("enabled", False)),
        webhook_type=str(webhook.get("type") or "generic"),
        webhook_url=str(webhook.get("url") or ""),
    )


def save_notify_settings(settings: NotifySettings) -> None:
    """设置页保存通知配置（写入文件后即生效，无需重启）"""
    write_json_config(
        NOTIFY_CONFIG_FILE,
        {
            "events": settings.resolved_events(),
            "webhook": {
                "enabled": settings.webhook_enabled,
                "type": settings.webhook_type,
                "url": settings.webhook_url,
            },
        },
    )
