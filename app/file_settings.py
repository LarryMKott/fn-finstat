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

from app.config import DATA_DIR, harden_perms, read_json_config, write_json_config
from app.utils.crypto import decrypt_value, encrypt_value

# ==================================================================
# 1. AI 智能分类（DeepSeek）：设置页「智能分类」卡片读写 ai_config.json
# ==================================================================

AI_CONFIG_FILE = DATA_DIR / "ai_config.json"

AI_DEFAULT_BASE_URL = "https://api.deepseek.com"
AI_DEFAULT_MODEL = "deepseek-chat"

# ---- 多供应商注册表（OpenAI 兼容协议）----
# 所有预置供应商均走 {base_url}/chat/completions + Bearer 认证的 OpenAI 兼容
# 协议（现有 DeepSeek 通道零改动即可复用）；custom 供自部署/其他兼容端点，
# base_url 与模型必须显式填写。models 仅为快捷预置，模型名始终可手填——
# 各家上新模型不应等本应用发版跟进。
AI_PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-flash", "deepseek-reasoner"],
        "key_url": "https://platform.deepseek.com",
    },
    "moonshot": {
        "label": "Moonshot（Kimi）",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "kimi-k2-0905-preview"],
        "key_url": "https://platform.moonshot.cn",
    },
    "zhipu": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4.5", "glm-4.5-air", "glm-4-flash"],
        "key_url": "https://open.bigmodel.cn",
    },
    "dashscope": {
        "label": "通义千问（百炼）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-max", "qwen-plus", "qwen-turbo"],
        "key_url": "https://bailian.console.aliyun.com",
    },
    "siliconflow": {
        "label": "SiliconFlow 硅基流动",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct"],
        "key_url": "https://siliconflow.cn",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o-mini", "gpt-4o"],
        "key_url": "https://platform.openai.com",
    },
    "custom": {
        "label": "自定义（OpenAI 兼容端点）",
        "base_url": "",
        "models": [],
        "key_url": "",
    },
}

AI_PROVIDER_IDS = tuple(AI_PROVIDERS)


def ai_provider_label(provider: str) -> str:
    """供应商显示名（未知取值给出可读兜底，不抛错）"""
    return (AI_PROVIDERS.get(provider) or {}).get("label") or "AI 服务"


def ai_provider_default(provider: str, field: str) -> str:
    """供应商预置默认值：field="base_url" 取预置地址，"model" 取首个预置模型"""
    info = AI_PROVIDERS.get(provider) or {}
    if field == "base_url":
        return str(info.get("base_url") or "")
    models = info.get("models") or []
    return str(models[0]) if models else ""


@dataclass
class AISettings:
    """AI 供应商配置（应用级共享，不按账号区分；api_key 加密存本地）"""

    api_key: str = ""
    provider: str = "deepseek"  # AI_PROVIDER_IDS 之一；历史配置缺省即 deepseek
    base_url: str = AI_DEFAULT_BASE_URL
    model: str = AI_DEFAULT_MODEL
    enabled: bool = False  # 导入账单时自动调用 AI 二次归类
    # ---- v1.1 自动化三开关（CAP-1/2/3，全部默认关闭，升级即安全）----
    # enabled 管的是「要不要调用 LLM 逐条归类」，下面三个管的是
    # 「LLM 能不能动你的分类体系」，语义互相独立
    auto_keyword_enabled: bool = False  # 归类请求顺带回填模型识别的高频关键词
    auto_category_enabled: bool = False  # CAP-3：允许 AI 归类时创建白名单外新分类
    auto_subcategory_enabled: bool = (
        False  # AI 新建分类时可挂到现有分类下（需同时开 auto_category_enabled）
    )

    @property
    def ready(self) -> bool:
        """已配置密钥即可发起调用；enabled 仅控制导入时的自动归类"""
        return bool(self.api_key.strip())


def load_ai_settings() -> AISettings:
    """读取 AI 配置；配置文件不存在时回退通用环境变量（本地开发可用 .env.dev 注入）

    provider 不在注册表内（手改配置文件）按 deepseek 兜底；base_url/model
    缺省时取所配供应商的预置默认值——切换供应商后清空地址即可回到预置端点。
    """
    data = read_json_config(
        AI_CONFIG_FILE,
        "AI",
        fallback={"api_key": os.environ.get("DEEPSEEK_API_KEY", "")},
    )
    provider = str(data.get("provider") or "deepseek").strip()
    if provider not in AI_PROVIDERS:
        provider = "deepseek"
    return AISettings(
        api_key=decrypt_value(str(data.get("api_key") or "")),
        provider=provider,
        base_url=str(data.get("base_url") or "").strip()
        or ai_provider_default(provider, "base_url")
        or AI_DEFAULT_BASE_URL,
        model=str(data.get("model") or "").strip()
        or ai_provider_default(provider, "model")
        or AI_DEFAULT_MODEL,
        enabled=bool(data.get("enabled", False)),
        # 缺字段自动 False：老配置文件升级即安全
        auto_keyword_enabled=bool(data.get("auto_keyword_enabled", False)),
        auto_category_enabled=bool(data.get("auto_category_enabled", False)),
        auto_subcategory_enabled=bool(data.get("auto_subcategory_enabled", False)),
    )


def save_ai_settings(settings: AISettings) -> None:
    """设置页保存 AI 配置（api_key 加密存储，写入文件后即生效）"""
    write_json_config(
        AI_CONFIG_FILE,
        {
            "api_key": encrypt_value(settings.api_key),
            "provider": (
                settings.provider if settings.provider in AI_PROVIDERS else "deepseek"
            ),
            "base_url": settings.base_url,
            "model": settings.model,
            "enabled": settings.enabled,
            "auto_keyword_enabled": settings.auto_keyword_enabled,
            "auto_category_enabled": settings.auto_category_enabled,
            "auto_subcategory_enabled": settings.auto_subcategory_enabled,
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
        webhook_url=decrypt_value(str(webhook.get("url") or "")),
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
                "url": encrypt_value(settings.webhook_url),
            },
        },
    )


# ---- 存量配置文件权限收敛 ----
# 0.7.x 之前的版本按 umask 落盘（644，同机其他用户可读），升级后首次启动
# 即收敛为 0600；此后每次保存经 write_json_config 保持 0600
for _legacy in (AI_CONFIG_FILE, NAS_CONFIG_FILE, NOTIFY_CONFIG_FILE):
    harden_perms(_legacy, 0o600)

# ---- 存量明文迁移（T-1.7）----
# v0.7.5 之前 api_key / webhook_url 以明文存储，启动时重新加密。
# read_json_config 返回的 dict 带有原始值；这里读→加密→写回。
for _legacy_path, _secret_fields in [
    (AI_CONFIG_FILE, ["api_key"]),
    (NOTIFY_CONFIG_FILE, ["webhook"]),
]:
    if not _legacy_path.exists():
        continue
    _raw = read_json_config(_legacy_path, "迁移")
    _dirty = False
    for _f in _secret_fields:
        _v = _raw.get(_f)
        if _f == "webhook" and isinstance(_v, dict):
            _url = _v.get("url", "")
            if _url and not _url.startswith("enc:"):
                _v["url"] = encrypt_value(_url)
                _dirty = True
        elif isinstance(_v, str) and _v and not _v.startswith("enc:"):
            _raw[_f] = encrypt_value(_v)
            _dirty = True
    if _dirty:
        write_json_config(_legacy_path, _raw)
