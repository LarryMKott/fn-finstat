"""消费分类接口（分类为全局共享；流水数量按当前飞牛账号统计）

权限约定：写操作（增/改/删）限管理员 —— 分类全局共享，重命名/删除会同步改写
**所有账号**的流水归类，属全局配置。独立部署/本地运行（网关未注入 user_id）
时管理员守卫自动放行，单机用户不受影响。读操作（列表/详情）保持公开。
"""

from fastapi import APIRouter, Depends

from app.api.deps import GatewayUser, get_gateway_user, require_admin
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
# 写操作要求管理员：分类是全局共享的，重命名/删除会同步改写**所有账号**的流水
# 归类，因此按「全局配置」的口径收口到管理员。
# 注意：这里必须用 require_admin 而不是 get_gateway_user —— 后者只解析身份、
# 从不拒绝请求（无身份头时按单机唯一用户放行），起不到任何守卫作用。
# 独立部署/本地运行（网关未注入 user_id）时 require_admin 自动放行，单机用户不受影响。
def create_category(
    payload: CategoryCreate, user: GatewayUser = Depends(require_admin)
):
    return ok(category_service.create_category(payload.name))


@router.put(
    "/{category_id}",
    response_model=ApiResponse[CategoryUpdateResult],
    summary="重命名分类（同步更新流水）",
)
def update_category(
    category_id: int,
    payload: CategoryCreate,
    user: GatewayUser = Depends(require_admin),
):
    return ok(category_service.update_category(category_id, payload.name))


@router.delete(
    "/{category_id}",
    response_model=ApiResponse[CategoryDeleteResult],
    summary="删除分类（其下流水归入「其他」）",
)
def delete_category(
    category_id: int, user: GatewayUser = Depends(require_admin)
):
    return ok(category_service.delete_category(category_id))
