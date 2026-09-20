"""配置层测试：连接参数修正与生效优先级（向导变量 > db_config.json > 通用环境变量 > 默认值）"""

import json
import os

import pytest

# 测试专用数据库密码：从环境变量读取（默认值为非可用凭据的占位串）
TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD", "test-password-not-usable")

from app.config import DBSettings, effective_db_settings, write_db_config_file

WIZARD_KEYS = [
    "wizard_db_type",
    "wizard_db_host",
    "wizard_db_port",
    "wizard_db_name",
    "wizard_db_user",
    "wizard_db_password",
]
GENERIC_KEYS = ["DB_TYPE", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]


@pytest.fixture()
def clean_db_env(monkeypatch, tmp_path):
    """清除向导/通用环境变量，并把 db_config.json 指向临时目录"""
    for key in WIZARD_KEYS + GENERIC_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("app.config.DB_CONFIG_FILE", tmp_path / "db_config.json")
    return tmp_path


def test_sanitized_fixes_unknown_type_and_port():
    fixed = DBSettings(db_type="oracle", port=0).sanitized()
    assert fixed.db_type == "sqlite"
    assert fixed.port == 0  # sqlite 无默认端口

    mysql = DBSettings(db_type="mysql", port=0).sanitized()
    assert mysql.port == 3306
    pg = DBSettings(db_type="postgresql", port=0).sanitized()
    assert pg.port == 5432


def test_sanitized_keeps_valid_port():
    assert DBSettings(db_type="mysql", port=3307).sanitized().port == 3307


def test_defaults_to_sqlite(clean_db_env):
    settings = effective_db_settings()
    assert settings.db_type == "sqlite"
    assert settings.name == "fn_finstat"


def test_generic_env_used_when_no_wizard(clean_db_env, monkeypatch):
    monkeypatch.setenv("DB_TYPE", "postgresql")
    monkeypatch.setenv("DB_HOST", "10.0.0.5")
    monkeypatch.setenv("DB_PORT", "5433")
    monkeypatch.setenv("DB_NAME", "fin")
    monkeypatch.setenv("DB_USER", "finuser")
    settings = effective_db_settings()
    assert (
        settings.db_type,
        settings.host,
        settings.port,
        settings.name,
        settings.user,
    ) == (
        "postgresql",
        "10.0.0.5",
        5433,
        "fin",
        "finuser",
    )


def test_wizard_overrides_generic_env(clean_db_env, monkeypatch):
    monkeypatch.setenv("DB_TYPE", "mysql")
    monkeypatch.setenv("DB_HOST", "10.0.0.9")
    monkeypatch.setenv("wizard_db_host", "192.168.1.9")
    monkeypatch.setenv("wizard_db_port", "3307")
    settings = effective_db_settings()
    assert settings.host == "192.168.1.9"
    assert settings.port == 3307


def test_config_file_overrides_generic_env(clean_db_env, monkeypatch):
    monkeypatch.setenv("DB_HOST", "10.0.0.9")
    write_db_config_file(
        DBSettings(db_type="mysql", host="10.1.1.1", port=3306, name="moved")
    )
    settings = effective_db_settings()
    assert settings.db_type == "mysql"
    assert settings.host == "10.1.1.1"
    assert settings.name == "moved"


def test_wizard_overrides_config_file(clean_db_env, monkeypatch):
    write_db_config_file(DBSettings(db_type="mysql", host="10.1.1.1", port=3306))
    monkeypatch.setenv("wizard_db_host", "192.168.1.9")
    assert effective_db_settings().host == "192.168.1.9"
    # 未显式设置的向导字段仍取配置文件值
    assert effective_db_settings().port == 3306


def test_corrupt_config_file_ignored(clean_db_env):
    from app.config import DB_CONFIG_FILE

    DB_CONFIG_FILE.write_text("{not valid json", encoding="utf-8")
    assert effective_db_settings().db_type == "sqlite"


def test_config_file_non_dict_ignored(clean_db_env):
    from app.config import DB_CONFIG_FILE

    DB_CONFIG_FILE.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert effective_db_settings().db_type == "sqlite"


def test_write_read_roundtrip(clean_db_env):
    settings = DBSettings(
        db_type="postgresql",
        host="db.local",
        port=5432,
        name="fin",
        user="admin",
        password=TEST_DB_PASSWORD,
    )
    write_db_config_file(settings)
    data = json.loads((clean_db_env / "db_config.json").read_text(encoding="utf-8"))
    assert data["password"].startswith("enc:")  # T-1.7 加密存储
    assert effective_db_settings().password == TEST_DB_PASSWORD
