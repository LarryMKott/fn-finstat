"""配置与数据文件的落盘权限（防泄漏）：明文机密仅属主可读写（0600）

飞牛 OS 是多用户系统：db_config.json 明文存数据库密码、ai_config.json 存
DeepSeek API Key、notify_config.json 的 Webhook URL 内含推送 Key。默认
umask 落盘为 644 时同机其他本地用户可读，这里验证统一收敛为 0600。

权限位仅 POSIX 有意义：Windows 开发机跳过（CI Linux 真实执行）。
"""

import os
import stat as stat_mod

import pytest

from app.config import DB_CONFIG_FILE, DATA_DIR, DBSettings, harden_perms
from app.config import write_db_config_file
from app.file_settings import (
    AI_CONFIG_FILE,
    NAS_CONFIG_FILE,
    NOTIFY_CONFIG_FILE,
    AISettings,
    NASImportSettings,
    NotifySettings,
    save_ai_settings,
    save_nas_settings,
    save_notify_settings,
)

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="权限位仅 POSIX 有意义，Windows 开发机跳过"
)


@pytest.fixture(autouse=True)
def _isolate_db_config(tmp_path, monkeypatch):
    """db_config.json 的写路径指向临时目录，避免测试污染本地真实连接覆盖文件
    （AI/NAS/通知三个路径由 conftest 的 ai_config_isolated 夹具统一隔离）"""
    monkeypatch.setattr("app.config.DB_CONFIG_FILE", tmp_path / "db_config.json")


def _mode(path) -> int:
    return stat_mod.S_IMODE(os.stat(path).st_mode)


def test_saved_config_files_are_owner_only(tmp_path):
    """四套落盘配置（含明文密码/Key）写入后均为 0600"""
    write_db_config_file(
        DBSettings(
            db_type="mysql", host="10.0.0.9", port=3307, name="fin", password="pw"
        )
    )
    save_ai_settings(AISettings(api_key="sk-test"))
    save_nas_settings(NASImportSettings(import_dir="/tmp/bills"))
    save_notify_settings(
        NotifySettings(
            webhook_enabled=True,
            webhook_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=SECRET",
        )
    )
    for path in (DB_CONFIG_FILE, AI_CONFIG_FILE, NAS_CONFIG_FILE, NOTIFY_CONFIG_FILE):
        assert path.exists(), path
        assert _mode(path) == 0o600, f"{path} 权限过宽：{oct(_mode(path))}"


def test_data_dir_is_owner_only():
    """数据目录仅应用账号可进入（含升级前已存在的旧目录，启动时收敛）"""
    assert _mode(DATA_DIR) == 0o700


def test_harden_perms_missing_path_is_noop(tmp_path):
    """路径不存在（如尚未生成的 SQLite WAL 文件）时静默跳过，不抛异常"""
    harden_perms(tmp_path / "not-existed.json", 0o600)
