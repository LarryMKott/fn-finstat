"""消费分类接口（分类为全局共享；流水数量按当前飞牛账号统计）

权限约定：写操作（增/改/删/关键词维护）限管理员 —— 分类全局共享，重命名/
删除会同步改写**所有账号**的流水归类，属全局配置。独立部署/本地运行（网关
未注入 user_id）时管理员守卫自动放行，单机用户不受影响。读操作（列表/详情/
树/关键词列表）保持公开。
"""

from fastapi import APIRouter, Depends

from app.api.deps import AdminUser, CurrentUser, request_db_session
from app.schemas.category import (
    CategoryCreate,
    CategoryDeleteResult,
    CategoryDetail,
    CategoryOut,
    CategoryTreeNode,
    CategoryUpdateResult,
    KeywordBatchCreate,
    KeywordMutateResult,
    KeywordOut,
    KeywordToggle,
)
from app.schemas.common import ApiResponse, ok
from app.services import audit_service, category_service, keyword_service

router = APIRouter(
    prefix="/api/category",
    tags=["分类管理"],
    dependencies=[Depends(request_db_session)],
)


@router.get("", response_model=ApiResponse[list[CategoryOut]], summary="获取分类列表")
def list_categories():
    return ok(category_service.list_categories())


# 注意：/tree 必须声明在 /{category_id} 之前，否则 "tree" 会被当作路径参数
# 解析（FastAPI 命中即停，int 校验失败直接 422，不会继续尝试后续路由）。
@router.get(
    "/tree",
    response_model=ApiResponse[list[CategoryTreeNode]],
    summary="分类树（含子孙 rollup 的流水数与支出）",
)
def category_tree(user: CurrentUser):
    """父子折叠输出；bill_count / expense_total 均为含子分类的累计值"""
    return ok(category_service.category_tree(user.user_id))


@router.get(
    "/{category_id}",
    response_model=ApiResponse[CategoryDetail],
    summary="分类详情（含当前账号的流水数量）",
)
def get_category(user: CurrentUser, category_id: int):
    return ok(category_service.get_category(category_id, user.user_id))


@router.post(
    "",
    response_model=ApiResponse[CategoryOut],
    status_code=201,
    summary="新增消费分类（可指定父分类建子类）",
)
# 写操作要求管理员：分类是全局共享的，重命名/删除会同步改写**所有账号**的流水
# 归类，因此按「全局配置」的口径收口到管理员。
# 注意：这里必须用 require_admin 而不是 get_gateway_user —— 后者只解析身份、
# 从不拒绝请求（无身份头时按单机唯一用户放行），起不到任何守卫作用。
# 独立部署/本地运行（网关未注入 user_id）时 require_admin 自动放行，单机用户不受影响。
def create_category(user: AdminUser, payload: CategoryCreate):
    created = category_service.create_category(
        payload.name, parent_id=payload.parent_id
    )
    where = f"（父分类 id={payload.parent_id}）" if payload.parent_id else ""
    audit_service.record(
        user.user_id,
        "category.create",
        "category",
        created["id"],
        "新增分类「" + created["name"] + "」" + where,
    )
    return ok(created)


@router.put(
    "/{category_id}",
    response_model=ApiResponse[CategoryUpdateResult],
    summary="重命名分类（同步更新流水）",
)
def update_category(user: AdminUser, category_id: int, payload: CategoryCreate):
    result = category_service.update_category(category_id, payload.name)
    audit_service.record(
        user.user_id,
        "category.rename",
        "category",
        category_id,
        "分类重命名 → 「"
        + payload.name
        + "」，同步 "
        + str(result["renamed_bills"])
        + " 条流水",
    )
    return ok(result)


@router.delete(
    "/{category_id}",
    response_model=ApiResponse[CategoryDeleteResult],
    summary="删除分类（其下流水归入「其他」；有子分类时拒绝）",
)
def delete_category(user: AdminUser, category_id: int):
    result = category_service.delete_category(category_id)
    audit_service.record(
        user.user_id,
        "category.delete",
        "category",
        category_id,
        "删除分类「"
        + result["name"]
        + "」，"
        + str(result["moved_bills"])
        + " 条流水并入默认分类",
    )
    return ok(result)


# ---- 分类关键词（v1.1 CAP-1）----
# 读公开（关键词表是匹配依据的可见性延伸），写与分类写同口径收口管理员。


@router.get(
    "/{category_id}/keywords",
    response_model=ApiResponse[list[KeywordOut]],
    summary="分类关键词列表（含来源与启用状态）",
)
def list_keywords(category_id: int):
    return ok(keyword_service.list_keywords(category_id))


@router.post(
    "/{category_id}/keywords",
    response_model=ApiResponse[KeywordMutateResult],
    summary="批量新增关键词（重名跳过不报错）",
)
def add_keywords(user: AdminUser, category_id: int, payload: KeywordBatchCreate):
    result = keyword_service.add_keywords(category_id, payload.keywords)
    audit_service.record(
        user.user_id,
        "category.keyword.add",
        "category",
        category_id,
        f"为分类新增 {result['added']} 个关键词",
    )
    return ok(result)


@router.put(
    "/keyword/{keyword_id}",
    response_model=ApiResponse[KeywordOut],
    summary="停用/启用关键词（停用后不参与匹配）",
)
def toggle_keyword(user: AdminUser, keyword_id: int, payload: KeywordToggle):
    row = keyword_service.set_keyword_enabled(keyword_id, payload.enabled)
    audit_service.record(
        user.user_id,
        "category.keyword.toggle",
        "category_keyword",
        keyword_id,
        ("启用" if payload.enabled else "停用") + f"关键词「{row['keyword']}」",
    )
    return ok(row)


@router.delete(
    "/keyword/{keyword_id}",
    response_model=ApiResponse[dict],
    summary="删除关键词",
)
def delete_keyword(user: AdminUser, keyword_id: int):
    deleted = keyword_service.delete_keyword(keyword_id)
    if deleted:
        audit_service.record(
            user.user_id,
            "category.keyword.delete",
            "category_keyword",
            keyword_id,
            "删除关键词",
        )
    return ok({"ok": deleted})
