"""消费分类业务逻辑"""
from fastapi import HTTPException

from app.config import DEFAULT_CATEGORY
from app.db.base import UniqueViolationError
from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO


def list_categories() -> list[dict]:
    return CategoryDAO.list_all()


def get_category(category_id: int) -> dict:
    cat = CategoryDAO.get_by_id(category_id)
    if cat is None:
        raise HTTPException(status_code=404, detail="分类不存在")
    return {**cat, "bill_count": BillDAO.count_by_category(cat["name"])}


def create_category(name: str) -> dict:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="分类名称不能为空")
    if len(name) > 20:
        raise HTTPException(status_code=400, detail="分类名称不能超过 20 个字符")
    if CategoryDAO.get_by_name(name):
        raise HTTPException(status_code=400, detail="分类已存在")
    category_id = CategoryDAO.create(name)
    if category_id is None:
        raise HTTPException(status_code=400, detail="分类已存在")
    return {"id": category_id, "name": name}


def update_category(category_id: int, name: str) -> dict:
    """重命名分类，并同步更新该分类下的所有流水"""
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="分类名称不能为空")
    if len(name) > 20:
        raise HTTPException(status_code=400, detail="分类名称不能超过 20 个字符")
    cat = CategoryDAO.get_by_id(category_id)
    if cat is None:
        raise HTTPException(status_code=404, detail="分类不存在")
    if cat["name"] == DEFAULT_CATEGORY and name != DEFAULT_CATEGORY:
        raise HTTPException(status_code=400, detail=f"默认分类「{DEFAULT_CATEGORY}」不可重命名")
    if cat["name"] == name:
        return {"id": category_id, "name": name, "renamed_bills": 0}
    dup = CategoryDAO.get_by_name(name)
    if dup and dup["id"] != category_id:
        raise HTTPException(status_code=400, detail="分类已存在")
    try:
        renamed = CategoryDAO.rename(category_id, name)
    except UniqueViolationError:
        raise HTTPException(status_code=400, detail="分类已存在")
    return {"id": category_id, "name": name, "renamed_bills": renamed}


def delete_category(category_id: int) -> dict:
    """删除分类，其下流水归入「其他」"""
    cat = CategoryDAO.get_by_id(category_id)
    if cat is None:
        raise HTTPException(status_code=404, detail="分类不存在")
    if cat["name"] == DEFAULT_CATEGORY:
        raise HTTPException(status_code=400, detail=f"默认分类「{DEFAULT_CATEGORY}」不可删除")
    moved = CategoryDAO.delete(category_id, fallback=DEFAULT_CATEGORY)
    return {"id": category_id, "name": cat["name"], "moved_bills": moved}
