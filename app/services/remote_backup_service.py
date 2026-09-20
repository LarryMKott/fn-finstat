"""异地备份服务（T-1.7）：将全量备份 JSON 推送到 WebDAV 服务器

- 零新依赖：urllib.request 标准库实现 WebDAV PUT（HTTP PUT + Basic Auth）
- 配置存 DATA_DIR/remote_backup_config.json（webdav_url / username / password）
- 密码加密存储（复用 utils.crypto）
- best-effort：推送失败只记日志 / 返回错误信息，不影响本地备份功能
"""

import logging
import urllib.request

from app.config import DATA_DIR, read_json_config, write_json_config
from app.utils.amount import round2  # noqa: F401 (占位)
from app.utils.crypto import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)

REMOTE_BACKUP_CONFIG_FILE = DATA_DIR / "remote_backup_config.json"
WEBDAV_TIMEOUT = 30


class RemoteBackupSettings:
    """异地备份配置（应用级共享；密码加密存储）"""

    def __init__(
        self,
        webdav_url: str = "",
        username: str = "",
        password: str = "",
    ):
        self.webdav_url = webdav_url.strip().rstrip("/")
        self.username = username
        self.password = password

    @property
    def ready(self) -> bool:
        return bool(self.webdav_url)

    def as_dict(self) -> dict:
        return {
            "webdav_url": self.webdav_url,
            "username": self.username,
            "has_password": bool(self.password),
        }


def load_remote_backup_settings() -> RemoteBackupSettings:
    data = read_json_config(REMOTE_BACKUP_CONFIG_FILE, "异地备份")
    return RemoteBackupSettings(
        webdav_url=str(data.get("webdav_url") or ""),
        username=str(data.get("username") or ""),
        password=decrypt_value(str(data.get("password") or "")),
    )


def save_remote_backup_settings(settings: RemoteBackupSettings) -> None:
    write_json_config(
        REMOTE_BACKUP_CONFIG_FILE,
        {
            "webdav_url": settings.webdav_url,
            "username": settings.username,
            "password": encrypt_value(settings.password),
        },
    )


def push_backup(backup_json: str, settings: RemoteBackupSettings) -> dict:
    """推送备份 JSON 到 WebDAV；返回 {ok, message}"""
    import base64

    if not settings.ready:
        return {"ok": False, "message": "WebDAV 地址未配置"}
    url = settings.webdav_url + "/fn-finstat-backup.json"
    req = urllib.request.Request(url, data=backup_json.encode("utf-8"), method="PUT")
    req.add_header("Content-Type", "application/json")
    if settings.username:
        cred = base64.b64encode(
            f"{settings.username}:{settings.password}".encode()
        ).decode()
        req.add_header("Authorization", f"Basic {cred}")
    try:
        with urllib.request.urlopen(req, timeout=WEBDAV_TIMEOUT) as resp:
            if resp.status < 300:
                return {"ok": True, "message": "推送成功"}
            return {"ok": False, "message": f"WebDAV 返回 {resp.status}"}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "message": f"WebDAV 返回 {exc.code}"}
    except Exception as exc:
        msg = str(exc)
        if settings.webdav_url in msg:
            msg = msg.replace(settings.webdav_url, "<webdav>")
        return {"ok": False, "message": f"连接失败：{msg}"}
