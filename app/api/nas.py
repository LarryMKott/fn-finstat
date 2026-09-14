"""NAS 目录导入接口：配置账单目录、浏览目录（自动识别来源）、按文件导入

新增（v0.4+）：
- 用户级账单目录授权：通过飞牛 trim 网关查询当前用户已授权目录、检查路径 ACL
- 三类失败（socket/token 缺失 → 网关不可用；网关返回非 0 → 被拒绝；协议异常）
  统一以 200 + status 字段携带 available=False + reason 表达，不抛 5xx，
  失败/未授权场景下旧 `update_config` / `get_config` / `list_files` / `import_file`
  行为不变，不影响其他功能
"""

from fastapi import APIRouter, Depends, Query

from app.api.deps import GatewayUser, get_gateway_user, require_admin
from app.schemas.common import ApiResponse, ok
from app.schemas.nas import (
    NasAclCheckRequest,
    NasAuthorizationStatus,
    NasConfigOut,
    NasConfigUpdate,
    NasDirectory,
    NasImportRequest,
)
from app.schemas.upload import ImportResult
from app.services import nas_authorization_service, nas_service

router = APIRouter(prefix="/api/nas", tags=["NAS 导入"])


@router.get(
    "/config", response_model=ApiResponse[NasConfigOut], summary="当前 NAS 账单目录配置"
)
def get_config(user: GatewayUser = Depends(get_gateway_user)):
    # 账单目录为应用级共享配置：完整路径只对管理员与单机模式回显，
    # 普通账号只拿到目录名，避免服务器目录布局外泄
    return ok(
        nas_service.get_config(reveal_full_path=user.is_admin or not user.user_id)
    )


@router.put(
    "/config",
    response_model=ApiResponse[NasConfigOut],
    summary="保存 NAS 账单目录（仅管理员：目录为应用级共享）",
)
def update_config(payload: NasConfigUpdate, user: GatewayUser = Depends(require_admin)):
    return ok(nas_service.update_config(payload, owner_user_id=user.user_id))


@router.get(
    "/files",
    response_model=ApiResponse[NasDirectory],
    summary="浏览账单目录（文件自动识别来源）",
)
def list_files(
    path: str = Query("", description="账单目录内相对路径，空为根目录"),
    user: GatewayUser = Depends(get_gateway_user),
):
    return ok(nas_service.list_directory(path))


@router.post(
    "/import",
    response_model=ApiResponse[ImportResult],
    summary="导入账单目录中的文件（自动识别来源）",
)
def import_file(
    payload: NasImportRequest, user: GatewayUser = Depends(get_gateway_user)
):
    return ok(nas_service.import_file(payload.path, user.user_id))


@router.get(
    "/authorization",
    response_model=ApiResponse[NasAuthorizationStatus],
    summary="当前用户已授权的账单目录（飞牛环境）",
    description=(
        "查询飞牛 trim 网关获取当前用户已授权给本应用的目录列表，"
        "以及管理员在「系统设置 > 应用」里授权给本应用的共享目录。"
        "共享目录查询失败只影响 shared_folders 字段，不会把整体打成不可用。"
        "飞牛环境 / 本地开发 / 鉴权失败 / 网关超时均不会抛 5xx，统一以"
        " status 字段携带 available/reason 表达。"
    ),
)
def get_authorization(user: GatewayUser = Depends(get_gateway_user)):
    return ok(
        NasAuthorizationStatus(
            **nas_authorization_service.get_user_authorization(user).to_dict()
        )
    )


@router.post(
    "/authorization/check-acl",
    response_model=ApiResponse[dict],
    summary="对账单目录内的一组路径做可读/可写/可删检查（飞牛环境）",
    description=(
        "返回 {path: {readable,writable,deletable}} 结构；trim 通道不可用时"
        " 全部按 True 放行（容灾语义）。失败信息写到服务端日志，绝不抛 5xx。"
    ),
)
def check_acl(
    payload: NasAclCheckRequest,
    user: GatewayUser = Depends(get_gateway_user),
):
    return ok(nas_authorization_service.check_path_acl(user, list(payload.paths)))
