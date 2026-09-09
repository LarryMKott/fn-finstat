"""应用配置：读取飞牛 OS(fnOS) 注入的环境变量与向导参数，本地开发回退到项目根目录

生产（fnOS）：
    TRIM_PKGVAR   持久化数据目录（升级/卸载保留）
    TRIM_PKGTMP   临时目录（系统回收）
    向导参数（wizard/install、wizard/config 收集，以环境变量形式注入应用进程）：
        wizard_db_type        数据库类型：sqlite（默认）/ mysql / postgresql
        wizard_db_host/port/name/user/password   外部数据库连接信息
        wizard_api_base_path  前后端接口地址前缀（默认 /app/fn-finstat）

本地开发：
    .env.dev 可设置同名向导变量或通用名（DB_TYPE/DB_HOST/.../API_BASE_PATH）
    数据 -> .local_data，临时文件 -> .local_tmp
"""
import os
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent            # .../fn-finstat/app
PROJECT_ROOT = APP_DIR.parent                        # .../fn-finstat

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

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8090"))

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB

DEFAULT_CATEGORIES = [
    "餐饮", "交通", "购物", "住房", "医疗", "娱乐", "其他",
    "数码", "通讯", "教育", "宠物",
]


def _env(*names: str, default: str = "") -> str:
    """依次取第一个非空环境变量（向导变量优先，通用名兜底）"""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


# ---- 数据库（向导参数 → 本地通用名 → 默认 sqlite）----
DB_TYPE = _env("wizard_db_type", "DB_TYPE", default="sqlite").lower()
if DB_TYPE not in ("sqlite", "mysql", "postgresql"):
    DB_TYPE = "sqlite"
DB_HOST = _env("wizard_db_host", "DB_HOST", default="127.0.0.1")
_db_port_raw = _env("wizard_db_port", "DB_PORT", default="")
_default_port = {"mysql": 3306, "postgresql": 5432}.get(DB_TYPE, 0)
DB_PORT = int(_db_port_raw) if _db_port_raw.strip().isdigit() else _default_port
DB_NAME = _env("wizard_db_name", "DB_NAME", default="fn_finstat")
DB_USER = _env("wizard_db_user", "DB_USER", default="root")
DB_PASSWORD = _env("wizard_db_password", "DB_PASSWORD", default="")

# ---- 前后端接口地址前缀（前端运行时自动适配，改动无需重新构建）----
API_BASE_PATH = _env("wizard_api_base_path", "API_BASE_PATH", default="/app/fn-finstat")
API_BASE_PATH = API_BASE_PATH.rstrip("/") or "/"
