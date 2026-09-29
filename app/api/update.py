"""应用更新接口：检查更新（只读远端信息）、下载目录配置、下载安装包到配置目录"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import AdminUser, CurrentUser, request_db_session
from app.schemas.common import ApiResponse, ok
from app.schemas.update import (
    UpdateCheckResult,
    UpdateDownloadDirOut,
    UpdateDownloadDirUpdate,
    UpdateDownloadRequest,
    UpdateDownloadResult,
)
from app.services import audit_service, update_service

router = APIRouter(
    prefix="/api/update",
    tags=["应用更新"],
    dependencies=[Depends(request_db_session)],
)


@router.get(
    "/check",
    response_model=ApiResponse[UpdateCheckResult],
    summary="检查应用更新（比对远端 Release 与本机版本）",
)
def check_update(
    _user: CurrentUser,
    refresh: bool = Query(
        False,
        description="true=忽略后端进程内缓存强制重新查询（用户手动点击「重新检查」时用）",
    ),
    channel: Optional[Literal["release", "dev"]] = Query(
        None,
        description="版本渠道：release=只比对正式版 / dev=与全部 Release 比较；"
        "缺省=跟随本机版本（正式包看正式版，测试包看测试线）",
    ),
    source: Literal["gitee", "github"] = Query(
        "github",
        description="发布站点：github（默认）/ gitee；两条流水线对同一 tag 各发一份 Release",
    ),
):
    """普通账号即可调用：只读远端公开版本信息，且不读取任何本机数据

    网络不可用（离线部署）时以 ok=false + message 返回原因，不抛错 —— 见
    app/schemas/update.py 的 UpdateCheckResult 说明。
    """
    return ok(
        update_service.check_for_update(refresh=refresh, channel=channel, source=source)
    )


@router.get(
    "/download-dir",
    response_model=ApiResponse[UpdateDownloadDirOut],
    summary="当前安装包下载目录配置",
)
def get_download_dir(user: CurrentUser):
    # 目录为应用级共享配置：完整路径只对管理员回显（配置与排障需要），
    # 普通账号只拿目录名与「是否已配置」，避免服务器目录布局外泄
    return ok(
        update_service.get_download_dir_config(
            reveal_full_path=user.is_admin or not user.user_id
        )
    )


@router.put(
    "/download-dir",
    response_model=ApiResponse[UpdateDownloadDirOut],
    summary="保存安装包下载目录（仅管理员：目录为应用级共享）",
)
def set_download_dir(user: AdminUser, payload: UpdateDownloadDirUpdate):
    result = update_service.set_download_dir(
        payload.download_dir, owner_user_id=user.user_id
    )
    audit_service.record(
        user.user_id,
        "update.download_dir",
        "update_config",
        None,
        "保存安装包下载目录配置",
    )
    return ok(result)


def _download_refused(reason: str) -> UpdateDownloadResult:
    """前置条件不满足时的失败结果（200 + ok=False，与检查失败同策略，不抛 5xx）"""
    return UpdateDownloadResult(ok=False, message=reason)


@router.post(
    "/download",
    response_model=ApiResponse[UpdateDownloadResult],
    summary="下载最新安装包（fpk）到配置的下载目录",
    description=(
        "由后端从 Release 附件解析下载地址（优先带版本号副本），流式落盘到"
        "**管理员配置的安装包下载目录**，落盘后用 Release 附带的 MD5SUMS.txt "
        "校验完整性。未配置 / 目录不可访问 / 网络异常均以 200 + ok=False + "
        "message 表达，不抛 5xx。"
    ),
)
def download_to_nas(_user: CurrentUser, payload: UpdateDownloadRequest):
    """普通账号可用：只消费管理员配置的下载目录，不做任何目录挑选

    下载地址由服务端自行解析，不接受调用方传入 URL；写盘走「临时文件 + 原子
    改名」，MD5 校验不匹配时丢弃残件，不会交付半截安装包。
    """
    dest_dir, problem = update_service.resolve_download_dir()
    if problem:
        return ok(_download_refused(problem))
    return ok(
        update_service.download_latest_release(
            dest_dir, source=payload.source, channel=payload.channel
        )
    )
