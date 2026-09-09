"""消费分类业务逻辑"""
from fastapi import HTTPException

from app.db.dao.category_dao import CategoryDAO


def list_categories() -> list[dict]:
    return CategoryDAO.list_all()


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
