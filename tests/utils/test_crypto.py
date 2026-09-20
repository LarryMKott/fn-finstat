"""配置敏感字段加密测试（T-1.7）：加密往返 / 明文兼容 / 主密钥 / 密码字段"""

import base64

from app.utils.crypto import decrypt_value, encrypt_value, is_encrypted


def test_roundtrip():
    """加密 → 解密 = 原文"""
    for pt in ("sk-test-123", "", "中文密钥 🔑", "a" * 1000):
        enc = encrypt_value(pt)
        if pt:
            assert enc.startswith("enc:")
            assert enc != pt
        assert decrypt_value(enc) == pt


def test_different_ciphertexts_for_same_plaintext():
    """同一明文两次加密产生不同密文（nonce 随机）"""
    e1 = encrypt_value("secret")
    e2 = encrypt_value("secret")
    assert e1 != e2
    assert decrypt_value(e1) == decrypt_value(e2) == "secret"


def test_decrypt_plaintext_passthrough():
    """非 enc: 前缀的值按明文原样返回（向后兼容旧版数据）"""
    assert decrypt_value("plain-api-key") == "plain-api-key"
    assert decrypt_value("") == ""


def test_decrypt_garbage_returns_original():
    """enc: 前缀但内容损坏 → 返回原值（不抛异常，降级为不可用而非崩溃）"""
    garbage = "enc:" + base64.b64encode(b"garbage-data-here").decode()
    assert decrypt_value(garbage) == garbage


def test_is_encrypted():
    assert is_encrypted("enc:abc") is True
    assert is_encrypted("plain") is False
    assert is_encrypted("") is False


def test_ai_config_api_key_encrypted_at_rest(client, db, tmp_path):
    """AI 配置的 api_key 在磁盘上以 enc: 前缀存储"""
    import json

    from app.file_settings import AI_CONFIG_FILE, save_ai_settings
    from app.file_settings import AISettings

    save_ai_settings(AISettings(api_key="sk-test-very-secret", enabled=True))
    raw = json.loads(AI_CONFIG_FILE.read_text(encoding="utf-8"))
    assert raw["api_key"].startswith("enc:")
    assert "sk-test" not in raw["api_key"]

    # 读回解密
    from app.file_settings import load_ai_settings

    loaded = load_ai_settings()
    assert loaded.api_key == "sk-test-very-secret"
