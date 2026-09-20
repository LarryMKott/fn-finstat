"""异地备份（T-1.7）的请求 / 响应模型"""

from pydantic import BaseModel, Field


class RemoteBackupConfigUpdate(BaseModel):
    """更新异地备份配置（WebDAV）"""

    webdav_url: str = Field(..., description="WebDAV 目录 URL")
    username: str = Field("", max_length=128, description="WebDAV 用户名（可选）")
    password: str = Field(
        "", max_length=255, description="WebDAV 密码（可选，加密存储）"
    )


class RemoteBackupConfigOut(BaseModel):
    """异地备份配置视图（密码不回传）"""

    webdav_url: str
    username: str
    has_password: bool = False


class RemoteBackupPushResult(BaseModel):
    """推送结果"""

    ok: bool
    message: str
