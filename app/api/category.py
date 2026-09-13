"""消费分类接口（分类为全局共享；流水数量按当前飞牛账号统计）"""

from fastapi import APIRouter, Depends

from app.api.deps import GatewayUser, get_gateway_user
from app.schemas.category import (
    CategoryCreate,
    CategoryDeleteResult,
    CategoryDetail,
    CategoryOut,
    CategoryUpdateResult,
)
from app.schemas.common import ApiResponse, ok
from app.services import category_service

router = APIRouter(prefix="/api/category", tags=["分类管理"])


@router.get("", response_model=ApiResponse[list[CategoryOut]], summary="获取分类列表")
def list_categories():
    return ok(category_service.list_categories())


@router.get(
    "/{category_id}",
    response_model=ApiResponse[CategoryDetail],
    summary="分类详情（含当前账号的流水数量）",
)
def get_category(category_id: int, user: GatewayUser = Depends(get_gateway_user)):
    return ok(category_service.get_category(category_id, user.user_id))


@router.post(
    "", response_model=ApiResponse[CategoryOut], status_code=201, summary="新增消费分类"
)
def create_category(payload: CategoryCreate):
    return ok(category_service.create_category(payload.name))


@router.put(
    "/{category_id}",
    response_model=ApiResponse[CategoryUpdateResult],
    summary="重命名分类（同步更新流水）",
)
def update_category(category_id: int, payload: CategoryCreate):
    return ok(category_service.update_category(category_id, payload.name))


@router.delete(
    "/{category_id}",
    response_model=ApiResponse[CategoryDeleteResult],
    summary="删除分类（其下流水归入「其他」）",
)
def delete_category(category_id: int):
    return ok(category_service.delete_category(category_id))
