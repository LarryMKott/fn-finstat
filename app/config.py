"""应用配置：读取飞牛 OS(fnOS) 注入的环境变量与向导参数，本地开发回退到项目根目录

生产（fnOS）：
    TRIM_PKGVAR   持久化数据目录（升级/卸载保留）
    TRIM_PKGTMP   临时目录（系统回收）
    向导参数（wizard/install、wizard/config 收集，以环境变量形式注入应用进程）：
        wizard_db_type        数据库类型：sqlite（默认）/ mysql / postgresql
        wizard_db_host/port/name/user/password   外部数据库连接信息
        wizard_api_base_path  前后端接口地址前缀（默认 /app/fn-finstat）
        wizard_port           HTTP 服务端口（默认 8090；fnOS 统一网关模式经 Unix Socket
                              通信不占用 TCP 端口，该端口用于独立部署/本地直接运行场景）

本地开发：
    .env.dev 可设置同名向导变量或通用名（DB_TYPE/DB_HOST/.../API_BASE_PATH）
    数据 -> .local_data，临时文件 -> .local_tmp

数据库连接的生效优先级（高 -> 低）：
    1. 向导环境变量（显式设置时，配合 config_callback 装驱动并重启）
    2. db_config.json（设置页「迁移并切换」成功后写入，重启后仍生效）
    3. 通用环境变量（本地开发 .env.dev）
    4. 默认值（本地 SQLite）
"""

import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# 配置文件读-改-写的进程内互斥：两个并发保存（读旧值→改→写回）会互相覆盖
_CONFIG_WRITE_LOCK = threading.Lock()


def _atomic_write_text(path: Path, text: str) -> None:
    """先写同目录临时文件再原子替换

    直接 write_text 时并发读到半截 JSON 会按「损坏/未配置」静默降级
    （AI 静默跳过、目录扫描空转）；os.replace 在同一文件系统内原子生效。
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


APP_DIR = Path(__file__).resolve().parent  # .../fn-finstat/app
PROJECT_ROOT = APP_DIR.parent  # .../fn-finstat

# 本地开发环境变量（.env.dev 不打包进 FPK），已存在环境变量优先
load_dotenv(PROJECT_ROOT / ".env.dev", override=False)

IS_FNOS = bool(os.environ.get("TRIM_PKGVAR"))

if IS_FNOS:
    DATA_DIR = Path(os.environ["TRIM_PKGVAR"]) / "finance"
    TMP_DIR = Path(os.environ.get("TRIM_PKGTMP", "/tmp")) / "fn-finstat"
else:
    DATA_DIR = PROJECT_ROOT / ".local_data" / "finance"
    TMP_DIR = PROJECT_ROOT / ".local_tmp"

DATA_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "bill.db"
# 设置页「迁移并切换」成功后写入的连接信息，重启后仍指向新数据库
DB_CONFIG_FILE = DATA_DIR / "db_config.json"

# ---- 应用与作者信息（设置页「关于」展示；版本号需与 manifest 的 version 同步更新）----
APP_NAME = "财务统计"
APP_VERSION = "0.6.0"
APP_AUTHOR = "zhangyilin_233"
APP_AUTHOR_URL = "https://gitee.com/zhangyilin_233"
APP_REPO_URL = "https://gitee.com/zhangyilin_233/fn-finstat"

# ---- 运行日志文件（fnOS 由 cmd/main 注入 LOG_FILE；本地默认项目根 app.log）----
# 应用写日志与设置页「运行日志」查看/下载共用此路径
LOG_PATH = Path(os.environ.get("LOG_FILE") or (PROJECT_ROOT / "app.log"))

# uvicorn 监听地址（仅独立部署 / 本地直接运行生效 —— fnOS 网关模式走 Unix Socket，
# 不占用 TCP 端口，此值不参与）。
# 默认只绑回环：应用把 X-Trim-Userid / X-Trim-Isadmin 请求头当作网关注入的可信身份
# （见 app/api/deps.py），绑 0.0.0.0 时局域网内任何人都能伪造这两个头直接成为管理员
# —— 可导出全量备份、覆盖恢复数据、改数据库与 AI 配置、下载运行日志。
# 确需局域网/公网访问时显式设 HOST=0.0.0.0，并自行确保网络可信或前置反代做鉴权。
HOST = os.environ.get("HOST", "127.0.0.1")

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB
# 文案用 MB 上限（错误提示三处共用，改 MAX_UPLOAD_SIZE 后提示自动跟随）
MAX_UPLOAD_SIZE_MB = MAX_UPLOAD_SIZE // (1024 * 1024)

DEFAULT_CATEGORIES = [
    "餐饮",
    "交通",
    "购物",
    "住房",
    "医疗",
    "娱乐",
    "其他",
    "数码",
    "通讯",
    "教育",
    "宠物",
]

# 自动归类与空分类归一化的兜底分类（受保护不可删改）
DEFAULT_CATEGORY = "其他"

SUPPORTED_DB_TYPES = ("sqlite", "mysql", "postgresql")
_DEFAULT_PORTS = {"mysql": 3306, "postgresql": 5432, "sqlite": 0}


@dataclass
class DBSettings:
    """数据库连接参数（sqlite 时 host/port/user/password 忽略）"""

    db_type: str = "sqlite"
    host: str = "127.0.0.1"
    port: int = 0
    name: str = "fn_finstat"
    user: str = "root"
    password: str = ""

    def sanitized(self) -> "DBSettings":
        """修正非法值：未知类型回退 sqlite，端口回退该类型默认值"""
        db_type = self.db_type if self.db_type in SUPPORTED_DB_TYPES else "sqlite"
        port = self.port if self.port > 0 else _DEFAULT_PORTS[db_type]
        return DBSettings(db_type, self.host, port, self.name, self.user, self.password)


def _env(*names: str, default: str = "") -> str:
    """依次取第一个非空环境变量（向导变量优先，通用名兜底）"""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


# ---- HTTP 服务端口（向导参数 → 通用环境变量 → 默认 8090）----
_port_raw = _env("wizard_port", "PORT", default="8090").strip()
PORT = int(_port_raw) if _port_raw.isdigit() and 0 < int(_port_raw) < 65536 else 8090


def _wizard_explicit(name: str) -> str:
    """向导注入的变量（仅显式存在且非空时返回，避免默认值压过设置页覆盖）"""
    return _env(name, default="").strip()


def _read_db_config_file() -> dict:
    """读取设置页写入的连接覆盖文件；缺失或损坏时返回空 dict（回退默认优先级）"""
    try:
        data = json.loads(DB_CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("数据库配置覆盖文件损坏，已忽略：%s", DB_CONFIG_FILE)
        return {}


def effective_db_settings() -> DBSettings:
    """计算生效的数据库连接参数（优先级见模块 docstring）"""
    file_cfg = _read_db_config_file()

    def pick(wizard_key: str, file_key: str, generic: str, default: str) -> str:
        wizard = _wizard_explicit(f"wizard_{wizard_key}")
        if wizard:
            return wizard
        if file_cfg.get(file_key) not in (None, ""):
            return str(file_cfg[file_key])
        return _env(generic, default=default)

    port_raw = pick("db_port", "port", "DB_PORT", "0").strip()
    return DBSettings(
        db_type=pick("db_type", "db_type", "DB_TYPE", "sqlite").lower(),
        host=pick("db_host", "host", "DB_HOST", "127.0.0.1"),
        port=int(port_raw) if port_raw.isdigit() else 0,
        name=pick("db_name", "name", "DB_NAME", "fn_finstat"),
        user=pick("db_user", "user", "DB_USER", "root"),
        password=pick("db_password", "password", "DB_PASSWORD", ""),
    ).sanitized()


def write_db_config_file(settings: DBSettings) -> None:
    """设置页切换成功后持久化连接信息（向导显式参数仍优先于此文件）"""
    with _CONFIG_WRITE_LOCK:
        _atomic_write_text(
            DB_CONFIG_FILE,
            json.dumps(
                {
                    "db_type": settings.db_type,
                    "host": settings.host,
                    "port": settings.port,
                    "name": settings.name,
                    "user": settings.user,
                    "password": settings.password,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )


# 启动时的生效配置；运行期切换数据库见 app/db/base.init_db 与设置页「迁移并切换」
DB = effective_db_settings()

# ---- 前后端接口地址前缀（前端运行时自动适配，改动无需重新构建）----
API_BASE_PATH = _env("wizard_api_base_path", "API_BASE_PATH", default="/app/fn-finstat")
API_BASE_PATH = API_BASE_PATH.rstrip("/") or "/"


# ---- NAS 目录导入配置：导入页写入 nas_config.json，重启后仍生效 ----
NAS_CONFIG_FILE = DATA_DIR / "nas_config.json"
# NAS 目录里允许导入的账单文件后缀（微信 xlsx、其余平台 csv）
NAS_IMPORT_EXTS = (".csv", ".xlsx")
# 单个账单文件大小上限（与上传一致；NAS 本地读取同样限制，避免误导入超大文件）
NAS_MAX_FILE_SIZE = MAX_UPLOAD_SIZE

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
    try:
        data = json.loads(AI_CONFIG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AISettings(api_key=os.environ.get("DEEPSEEK_API_KEY", ""))
    except Exception:
        logger.warning("AI 配置文件损坏，已忽略：%s", AI_CONFIG_FILE)
        data = {}
    if not isinstance(data, dict):
        data = {}
    return AISettings(
        api_key=str(data.get("api_key") or ""),
        base_url=str(data.get("base_url") or "").strip() or AI_DEFAULT_BASE_URL,
        model=str(data.get("model") or "").strip() or AI_DEFAULT_MODEL,
        enabled=bool(data.get("enabled", False)),
    )


def save_ai_settings(settings: AISettings) -> None:
    """设置页保存 AI 配置（写入文件后即生效，无需重启）"""
    with _CONFIG_WRITE_LOCK:
        _atomic_write_text(
            AI_CONFIG_FILE,
            json.dumps(
                {
                    "api_key": settings.api_key,
                    "base_url": settings.base_url,
                    "model": settings.model,
                    "enabled": settings.enabled,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )


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
    try:
        data = json.loads(NAS_CONFIG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return NASImportSettings()
    except Exception:
        logger.warning("NAS 导入配置文件损坏，已忽略：%s", NAS_CONFIG_FILE)
        data = {}
    if not isinstance(data, dict):
        data = {}
    return NASImportSettings(
        import_dir=str(data.get("import_dir") or "").strip(),
        owner_user_id=str(data.get("owner_user_id") or "").strip(),
    )


def save_nas_settings(settings: NASImportSettings) -> None:
    """导入页保存 NAS 目录配置（写入文件后即生效，无需重启）"""
    with _CONFIG_WRITE_LOCK:
        _atomic_write_text(
            NAS_CONFIG_FILE,
            json.dumps(
                {
                    "import_dir": settings.import_dir,
                    "owner_user_id": settings.owner_user_id,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
