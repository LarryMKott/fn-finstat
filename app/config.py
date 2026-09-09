"""应用配置：读取飞牛 OS(fnOS) 注入的环境变量，本地开发回退到项目根目录

生产（fnOS）：
    TRIM_PKGVAR   持久化数据目录（升级/卸载保留），数据库写入 {TRIM_PKGVAR}/finance
    TRIM_PKGTMP   临时目录（系统回收），上传文件写入 {TRIM_PKGTMP}/fn-finstat
    TRIM_APPDEST  只读源码目录，禁止写入

本地开发：
    数据      -> <项目根>/.local_data
    临时文件  -> <项目根>/.local_tmp
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
