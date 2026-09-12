"""外部数据库驱动的可用性检查与按需安装（设置页「测试连接/迁移」时触发）

fnOS 安装期由 cmd/config_callback 按向导参数装驱动；设置页允许不重启直接切库，
因此驱动缺失时在应用进程内用当前解释器 pip 安装（与 config_callback 相同的包来源）。
"""

import importlib
import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

# db_type -> (检查用的模块名, pip 包列表)
_DRIVER_SPECS = {
    "mysql": ("pymysql", ["PyMySQL>=1.1", "cryptography>=42"]),
    "postgresql": ("psycopg2", ["psycopg2-binary>=2.9"]),
}
_INSTALL_TIMEOUT_SECONDS = 300


def _importable(module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except ImportError:
        return False


def ensure_driver(db_type: str) -> None:
    """确保驱动可用；缺失时尝试自动安装，失败抛 RuntimeError（服务层转 400）"""
    spec = _DRIVER_SPECS.get(db_type)
    if spec is None:  # sqlite 无需驱动
        return
    module, packages = spec
    if _importable(module):
        return
    logger.info("未安装 %s 驱动，开始自动安装：%s", db_type, " ".join(packages))
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                "--disable-pip-version-check",
                *packages,
            ],
            check=True,
            capture_output=True,
            timeout=_INSTALL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"驱动自动安装超时，请手动执行：pip install {' '.join(packages)}"
        ) from exc
    except (subprocess.CalledProcessError, OSError) as exc:
        raise RuntimeError(
            f"驱动自动安装失败，请手动执行：pip install {' '.join(packages)}"
        ) from exc
    if not _importable(module):
        raise RuntimeError(f"驱动安装后仍不可用（{module}），请重启应用后重试")
    logger.info("%s 驱动安装完成", db_type)
