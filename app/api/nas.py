"""NAS 目录导入接口：配置账单目录、浏览目录（自动识别来源）、按文件导入"""

from fastapi import APIRouter, Depends, Query

from app.api.deps import GatewayUser, get_gateway_user, require_admin
from app.schemas.nas import (
    NasConfigOut,
    NasConfigUpdate,
    NasDirectory,
    NasImportRequest,
)
from app.schemas.upload import ImportResult
from app.services import nas_service

router = APIRouter(prefix="/api/nas", tags=["NAS 导入"])


@router.get("/config", response_model=NasConfigOut, summary="当前 NAS 账单目录配置")
def get_config(user: GatewayUser = Depends(get_gateway_user)):
    return nas_service.get_config()


@router.put(
    "/config",
    response_model=NasConfigOut,
    summary="保存 NAS 账单目录（仅管理员：目录为应用级共享）",
)
def update_config(
    payload: NasConfigUpdate, user: GatewayUser = Depends(require_admin)
):
    return nas_service.update_config(payload)


@router.get(
    "/files", response_model=NasDirectory, summary="浏览账单目录（文件自动识别来源）"
)
def list_files(
    path: str = Query("", description="账单目录内相对路径，空为根目录"),
    user: GatewayUser = Depends(get_gateway_user),
):
    return nas_service.list_directory(path)


@router.post(
    "/import",
    response_model=ImportResult,
    summary="导入账单目录中的文件（自动识别来源）",
)
def import_file(
    payload: NasImportRequest, user: GatewayUser = Depends(get_gateway_user)
):
    return nas_service.import_file(payload.path, user.user_id)
