"""异地备份接口（T-1.7，仅管理员）"""

from fastapi import APIRouter, Depends

from app.api.deps import AdminUser, request_db_session
from app.schemas.common import ApiResponse, ok
from app.schemas.remote_backup import (
    RemoteBackupConfigOut,
    RemoteBackupConfigUpdate,
    RemoteBackupPushResult,
)
from app.services import remote_backup_service

router = APIRouter(
    prefix="/api/remote-backup",
    tags=["异地备份"],
    dependencies=[Depends(request_db_session)],
)


@router.get(
    "/config",
    response_model=ApiResponse[RemoteBackupConfigOut],
    summary="异地备份配置（密码不回传）",
)
def get_config(user: AdminUser):
    s = remote_backup_service.load_remote_backup_settings()
    return ok(
        RemoteBackupConfigOut(
            webdav_url=s.webdav_url,
            username=s.username,
            has_password=bool(s.password),
        )
    )


@router.put(
    "/config",
    response_model=ApiResponse[RemoteBackupConfigOut],
    summary="保存异地备份配置（仅管理员，密码加密存储）",
)
def save_config(user: AdminUser, payload: RemoteBackupConfigUpdate):
    s = remote_backup_service.RemoteBackupSettings(
        webdav_url=payload.webdav_url,
        username=payload.username,
        password=payload.password,
    )
    remote_backup_service.save_remote_backup_settings(s)
    return ok(
        RemoteBackupConfigOut(
            webdav_url=s.webdav_url,
            username=s.username,
            has_password=bool(s.password),
        )
    )


@router.post(
    "/push",
    response_model=ApiResponse[RemoteBackupPushResult],
    summary="推送当前全量备份到异地（WebDAV，仅管理员）",
)
def push_backup(user: AdminUser):
    """将 export_backup 的 JSON 推送到已配置的 WebDAV 地址"""
    import json

    from app.services import backup_service

    settings = remote_backup_service.load_remote_backup_settings()
    data = backup_service.export_backup()
    backup_json = json.dumps(data, ensure_ascii=False, indent=1)
    res = remote_backup_service.push_backup(backup_json, settings)
    return ok(RemoteBackupPushResult(**res))
