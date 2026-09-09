"""消费分类接口"""
from fastapi import APIRouter

from app.schemas.category import CategoryCreate, CategoryDetail, CategoryOut, CategoryUpdateResult
from app.services import category_service

router = APIRouter(prefix="/api/category", tags=["分类管理"])


@router.get("", response_model=list[CategoryOut], summary="获取分类列表")
def list_categories():
    return category_service.list_categories()


@router.get("/{category_id}", response_model=CategoryDetail, summary="分类详情（含流水数量）")
def get_category(category_id: int):
    return category_service.get_category(category_id)


@router.post("", response_model=CategoryOut, status_code=201, summary="新增消费分类")
def create_category(payload: CategoryCreate):
    return category_service.create_category(payload.name)


@router.put("/{category_id}", response_model=CategoryUpdateResult, summary="重命名分类（同步更新流水）")
def update_category(category_id: int, payload: CategoryCreate):
    return category_service.update_category(category_id, payload.name)


@router.delete("/{category_id}", summary="删除分类（其下流水归入「其他」）")
def delete_category(category_id: int):
    return category_service.delete_category(category_id)
