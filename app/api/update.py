"""应用更新检查接口（只读，不做任何本地写入）"""

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, request_db_session
from app.schemas.common import ApiResponse, ok
from app.schemas.update import UpdateCheckResult
from app.services import update_service

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
):
    """普通账号即可调用：只读远端公开版本信息，且不读取任何本机数据

    网络不可用（离线部署）时以 ok=false + message 返回原因，不抛错 —— 见
    app/schemas/update.py 的 UpdateCheckResult 说明。
    """
    return ok(update_service.check_for_update(refresh=refresh))
