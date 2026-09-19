"""应用静态配置：读取飞牛 OS(fnOS) 注入的环境变量与向导参数，本地开发回退到项目根目录

本模块只放「进程启动时定型、运行期只读」的配置；设置页/导入页在运行期读写的
文件配置（AI、NAS 目录、通知）见 app/file_settings.py。数据库覆盖文件
db_config.json 因与环境变量优先级强耦合，仍留在本模块 §9。

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

目录（按维护动线编号，改配置先查这里）：
    1. 路径与运行形态          数据/临时目录、fnOS 判定、.env.dev 加载
    2. 环境变量读取辅助        _env（向导变量优先，通用名兜底）
    3. 应用元信息              「关于」页展示的名称/版本/作者/仓库
    4. HTTP 服务与来源信任    HOST/PORT/接口前缀/Host 白名单/无头放行开关
    5. 运行日志                日志路径与轮转参数
    6. 上传与导入限制          上传大小、NAS 导入后缀与大小
    7. 领域默认值              预置分类、默认分类、默认账本名
    8. JSON 配置文件读写基建  原子写 + 损坏降级（本模块与 file_settings 共用）
    9. 数据库连接              连接参数四级优先级解析、覆盖文件、连接池/驱动参数
"""

import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 1. 路径与运行形态
# ------------------------------------------------------------------

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


def harden_perms(path: Path, mode: int) -> None:
    """收紧文件/目录权限（尽力而为）：POSIX 上设为仅属主可读写/进入

    飞牛 OS 是多用户系统，配置文件里明文存着数据库密码、AI API Key、
    Webhook 推送 Key，数据目录里是全部账单——默认 umask 落盘是 644/755，
    同机其他本地用户可读。对不存在的路径跳过；Windows/特殊文件系统无
    对应语义或 chmod 失败时静默忽略（防泄漏是纵深防御，不阻塞启动）。
    """
    try:
        os.chmod(path, mode)
    except OSError:
        pass


# 数据/临时目录仅应用账号可进入（目录已存在时同样收敛，覆盖升级前的旧权限）
harden_perms(DATA_DIR, 0o700)
harden_perms(TMP_DIR, 0o700)


# ------------------------------------------------------------------
# 2. 环境变量读取辅助
# ------------------------------------------------------------------


def _env(*names: str, default: str = "") -> str:
    """依次取第一个非空环境变量（向导变量优先，通用名兜底）"""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


# ------------------------------------------------------------------
# 3. 应用元信息（设置页「关于」展示）
# ------------------------------------------------------------------

# 版本号的唯一真实来源是根目录 VERSION 文件；构建打包时 sync_version.py
# 自动将 VERSION 的值同步到 manifest 与此处的 APP_VERSION，故修改版本号
# 只需编辑 VERSION 文件即可，无需同步多处。
APP_NAME = "财务统计"
APP_VERSION = "0.7.0"
APP_AUTHOR = "zhangyilin_233"
APP_AUTHOR_URL = "https://gitee.com/zhangyilin_233"
APP_REPO_URL = "https://gitee.com/zhangyilin_233/fn-finstat"

# ------------------------------------------------------------------
# 4. HTTP 服务与来源信任
# ------------------------------------------------------------------

# uvicorn 监听地址（仅独立部署 / 本地直接运行生效 —— fnOS 网关模式走 Unix Socket，
# 不占用 TCP 端口，此值不参与）。
# 默认只绑回环：应用把 X-Trim-Userid / X-Trim-Isadmin 请求头当作网关注入的可信身份
# （见 app/api/deps.py），绑 0.0.0.0 时局域网内任何人都能伪造这两个头直接成为管理员
# —— 可导出全量备份、覆盖恢复数据、改数据库与 AI 配置、下载运行日志。
# 确需局域网/公网访问时显式设 HOST=0.0.0.0，并自行确保网络可信或前置反代做鉴权。
HOST = os.environ.get("HOST", "127.0.0.1")

# HTTP 服务端口（向导参数 → 通用环境变量 → 默认 8090；仅独立部署/本地运行生效）
_port_raw = _env("wizard_port", "PORT", default="8090").strip()
PORT = int(_port_raw) if _port_raw.isdigit() and 0 < int(_port_raw) < 65536 else 8090

# 回环地址集合（小写）：独立部署 Host 白名单的基础与「HOST 是否非回环」的判定源
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

# HOST 为通配/空地址时无法枚举局域网访问名（IP/主机名/mDNS），Host 白名单随之
# 关闭（仍保留写方法 Origin 同源校验，见 core/middleware.py 的 SourceGuardMiddleware）
HOST_IS_WILDCARD = HOST.strip().lower() in ("", "*", "0.0.0.0", "::")

# Host 白名单：HOST 为具体地址时 = 回环集合 + HOST 本身（局域网按本机地址访问不被拦）；
# 通配地址时仅回环集合（且校验关闭）。fnOS 模式不启用白名单，由网关负责来源。
ALLOWED_HOSTS = frozenset(LOOPBACK_HOSTS) | (
    frozenset() if HOST_IS_WILDCARD else frozenset({HOST.strip().lower()})
)

# fnOS 网关模式下默认拒绝缺失网关身份头（X-Trim-*）的请求（HTTP 401）——
# 空身份曾等同唯一用户全量放行，网关一旦转发未注入头的请求即整体提权。
# 设备上经 curl --unix-socket 直连 app.sock 排障时可临时开启本开关恢复旧行为；
# 开启即扩大信任面（无头请求等同管理员），应用启动日志会给出醒目提示。
ALLOW_HEADERLESS = os.environ.get("FNOS_ALLOW_HEADERLESS", "").strip().lower() in (
    "1",
    "true",
    "yes",
)

# 前后端接口地址前缀（前端运行时自动适配，改动无需重新构建）
API_BASE_PATH = _env("wizard_api_base_path", "API_BASE_PATH", default="/app/fn-finstat")
API_BASE_PATH = API_BASE_PATH.rstrip("/") or "/"

# ------------------------------------------------------------------
# 5. 运行日志（fnOS 由 cmd/main 注入 LOG_FILE；本地默认项目根 app.log）
# ------------------------------------------------------------------

# 应用写日志与设置页「运行日志」查看/下载共用此路径
LOG_PATH = Path(os.environ.get("LOG_FILE") or (PROJECT_ROOT / "app.log"))
# 轮转参数：单文件上限与保留份数（设置页日志尾部读取量按上限推导）
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 3

# ------------------------------------------------------------------
# 6. 上传与导入限制
# ------------------------------------------------------------------

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB
# 文案用 MB 上限（错误提示三处共用，改 MAX_UPLOAD_SIZE 后提示自动跟随）
MAX_UPLOAD_SIZE_MB = MAX_UPLOAD_SIZE // (1024 * 1024)

# NAS 目录里允许导入的账单文件后缀（微信 xlsx、其余平台 csv）
NAS_IMPORT_EXTS = (".csv", ".xlsx")
# 单个账单文件大小上限（与上传一致；NAS 本地读取同样限制，避免误导入超大文件）
NAS_MAX_FILE_SIZE = MAX_UPLOAD_SIZE

# ------------------------------------------------------------------
# 7. 领域默认值
# ------------------------------------------------------------------

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

# 账本维度（T-7.1）默认账本名：升级与全新安装共用，历史数据统一挂载其上。
# 这是唯一「受保护」的账本：不可删除，删除其他账本时其数据并入本账本。
DEFAULT_LEDGER_NAME = "默认账本"

# ------------------------------------------------------------------
# 8. JSON 配置文件读写基建
#    供本模块（db_config.json）与 app/file_settings.py（AI/NAS/通知）共用；
#    新增一个「设置页可写、落盘 JSON」的配置时：文件路径常量 + dataclass +
#    load/save 三件套写进 file_settings.py，读写一律走下面两个函数。
# ------------------------------------------------------------------

# 配置文件读-改-写的进程内互斥：两个并发保存（读旧值→改→写回）会互相覆盖
_CONFIG_WRITE_LOCK = threading.Lock()


def _atomic_write_text(path: Path, text: str) -> None:
    """先写同目录临时文件再原子替换，落盘权限收敛为仅属主可读写（0600）

    直接 write_text 时并发读到半截 JSON 会按「损坏/未配置」静默降级
    （AI 静默跳过、目录扫描空转）；os.replace 在同一文件系统内原子生效。
    权限在临时文件上设置、随 replace 保留（rename 不改 inode 权限）——
    所有配置文件（含明文密码/Key 的 db/ai/nas/notify config）都从这里落盘，
    是权限收敛的唯一收口点。
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    harden_perms(tmp, 0o600)
    os.replace(tmp, path)


def read_json_config(path: Path, label: str, fallback: dict | None = None) -> dict:
    """读取一个 JSON 配置文件，统一「缺失/损坏静默降级」语义

    - 文件缺失：返回 fallback（默认空 dict），不告警（未配置是常态）
    - 文件损坏或内容不是 JSON 对象：记 warning 后返回空 dict（按未配置处理）
    label 用于日志文案（如 "AI"、"NAS 导入"）。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(fallback) if fallback else {}
    except Exception:
        logger.warning("%s配置文件损坏，已忽略：%s", label, path)
        return {}
    return data if isinstance(data, dict) else {}


def write_json_config(path: Path, payload: dict) -> None:
    """进程内互斥 + 原子替换写入 JSON 配置（读-改-写并发保存不互相覆盖）"""
    with _CONFIG_WRITE_LOCK:
        _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


# ------------------------------------------------------------------
# 9. 数据库连接
# ------------------------------------------------------------------

DB_PATH = DATA_DIR / "bill.db"
# 设置页「迁移并切换」成功后写入的连接信息，重启后仍指向新数据库
DB_CONFIG_FILE = DATA_DIR / "db_config.json"
# 覆盖文件明文含外部数据库密码；已存在的旧文件在启动时一并收敛权限
# （本修复之前的版本按 644 落盘）
harden_perms(DB_CONFIG_FILE, 0o600)

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


def _wizard_explicit(name: str) -> str:
    """向导注入的变量（仅显式存在且非空时返回，避免默认值压过设置页覆盖）"""
    return _env(name, default="").strip()


def _read_db_config_file() -> dict:
    """读取设置页写入的连接覆盖文件；缺失或损坏时返回空 dict（回退默认优先级）"""
    return read_json_config(DB_CONFIG_FILE, "数据库配置覆盖")


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
    write_json_config(
        DB_CONFIG_FILE,
        {
            "db_type": settings.db_type,
            "host": settings.host,
            "port": settings.port,
            "name": settings.name,
            "user": settings.user,
            "password": settings.password,
        },
    )


# 启动时的生效配置；运行期切换数据库见 app/db/base.init_db 与设置页「迁移并切换」
DB = effective_db_settings()

# ---- 数据库连接池与驱动参数（engine.build_engine 使用，集中于此便于调优）----
# 个人 NAS 应用并发极低，小连接池即可；pool_recycle 需小于常见 MySQL
# wait_timeout（默认 8h），防长连接被服务端静默断开
DB_POOL_SIZE = 5
DB_MAX_OVERFLOW = 5
DB_POOL_RECYCLE = 1800  # 秒
DB_CONNECT_TIMEOUT = 10  # 秒
# SQLite 写锁等待（毫秒）：并发写瞬间排队而不是立刻报 database is locked
SQLITE_BUSY_TIMEOUT_MS = 5000
