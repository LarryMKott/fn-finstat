"""操作审计接口（T-7.6）：写操作留痕查询（只读）"""

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, request_db_session
from app.api.params import LedgerIdQuery  # noqa: F401  仅为复用既有参数别名风格
from app.schemas.audit import AuditLogPage
from app.schemas.common import ApiResponse, ok
from app.services import audit_service

router = APIRouter(
    prefix="/api/audit",
    tags=["操作审计"],
    dependencies=[Depends(request_db_session)],
)


@router.get(
    "",
    response_model=ApiResponse[AuditLogPage],
    summary="操作审计查询（管理员看全部，普通账号仅自己的操作）",
)
def list_audit(
    user: CurrentUser,
    limit: int = Query(200, ge=1, le=1000, description="返回条数上限"),
    offset: int = Query(0, ge=0, description="分页偏移"),
    action: Optional[str] = Query(None, description="按动作过滤，如 bill.update"),
    user_id: Optional[str] = Query(
        None, description="按操作人过滤（仅管理员生效；普通账号强制查自己）"
    ),
):
    """与 get_database_info 同口径：无网关身份（本地/独立部署）视为唯一用户管理员"""
    is_admin = not user.user_id or user.is_admin
    return ok(
        audit_service.list_logs(
            user.user_id,
            is_admin,
            limit=limit,
            offset=offset,
            action=action,
            target_user_id=user_id,
        )
    )
