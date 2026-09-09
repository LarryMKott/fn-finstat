"""消费分类接口"""
from fastapi import APIRouter

from app.schemas.category import CategoryCreate, CategoryOut
from app.services import category_service

router = APIRouter(prefix="/api/category", tags=["分类管理"])


@router.get("", response_model=list[CategoryOut], summary="获取分类列表")
def list_categories():
    return category_service.list_categories()


@router.post("", response_model=CategoryOut, status_code=201, summary="新增消费分类")
def create_category(payload: CategoryCreate):
    return category_service.create_category(payload.name)
