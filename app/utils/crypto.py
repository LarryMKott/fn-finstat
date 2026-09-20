"""配置敏感字段加密（T-1.7）：HMAC-SHA256 CTR + encrypt-then-MAC，零新依赖

保护目标：ai_config.json / notify_config.json / db_config.json 中明文存的
API Key / Webhook 推送 Key / 数据库口令，在 NAS 多用户环境下不被同机其他
本地用户读取。

方案：
- 主密钥：DATA_DIR/app_secret.key（32 字节随机，首次启动自动生成，0600）；
- 加密：HMAC-SHA256 CTR 模式流加密 + encrypt-then-MAC（先加密后认证）；
- 格式：base64(nonce[16] + ciphertext + mac[32])，明文值不加前缀原样存储，
  加密值以 "enc:" 前缀区分（向后兼容：读到非 enc: 值按明文处理）。

这是纵深防御的一层—— protects against「同机其他用户读配置文件」，
not against「root 读取 / 进程内存 dump」。
"""

import base64
import hashlib
import hmac
import os

_NONCE_LEN = 16
_MAC_LEN = 32
_ENC_PREFIX = "enc:"


_MASTER_KEY: bytes | None = None


def _master_key() -> bytes:
    """主密钥（懒加载：首次调用时从 app.config 取 DATA_DIR 并生成/读取密钥文件）"""
    global _MASTER_KEY
    if _MASTER_KEY is not None:
        return _MASTER_KEY
    from app.config import DATA_DIR, harden_perms

    key_file = DATA_DIR / "app_secret.key"
    if key_file.exists():
        key = key_file.read_bytes()
        if len(key) == 32:
            _MASTER_KEY = key
            return key
    key = os.urandom(32)
    key_file.write_bytes(key)
    harden_perms(key_file, 0o600)
    _MASTER_KEY = key
    return key


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """HMAC-SHA256 计数器模式：生成 length 字节密钥流"""
    blocks = []
    for i in range((length + 31) // 32):
        blocks.append(
            hmac.new(key, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
        )
    return b"".join(blocks)[:length]


def _derive(subpurpose: bytes) -> bytes:
    return hmac.new(_master_key(), subpurpose, hashlib.sha256).digest()


def encrypt_value(plaintext: str) -> str:
    """明文字符串 → "enc:" + base64(nonce + ciphertext + mac)"""
    if not plaintext:
        return plaintext
    pt = plaintext.encode("utf-8")
    enc_key = _derive(b"encrypt")
    mac_key = _derive(b"mac")
    nonce = os.urandom(_NONCE_LEN)
    ct = bytes(a ^ b for a, b in zip(pt, _keystream(enc_key, nonce, len(pt))))
    mac = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    return _ENC_PREFIX + base64.b64encode(nonce + ct + mac).decode("ascii")


def decrypt_value(stored: str) -> str:
    """ "enc:" + base64 → 明文字符串；非 enc: 前缀按明文原样返回（向后兼容）"""
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored
    try:
        raw = base64.b64decode(stored[len(_ENC_PREFIX) :])
        nonce, ct, mac = raw[:_NONCE_LEN], raw[_NONCE_LEN:-_MAC_LEN], raw[-_MAC_LEN:]
        mac_key = _derive(b"mac")
        expected = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected):
            return stored  # MAC 不匹配：返回原值（可能是密钥文件被更换）
        enc_key = _derive(b"encrypt")
        pt = bytes(a ^ b for a, b in zip(ct, _keystream(enc_key, nonce, len(ct))))
        return pt.decode("utf-8")
    except Exception:
        return stored


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(_ENC_PREFIX)
