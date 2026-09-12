"""消费分类服务层测试"""

import pytest
from fastapi import HTTPException

from app.services import category_service
from tests.conftest import USER_A, USER_B, make_bill_records
from app.db.dao.bill_dao import BillDAO


def test_create_strips_name(db):
    from app.db.dao.category_dao import CategoryDAO

    created = category_service.create_category("  新分类  ")
    assert created["name"] == "新分类"
    assert CategoryDAO.get_by_name("新分类") is not None


def test_create_validations(db):
    with pytest.raises(HTTPException) as e:
        category_service.create_category("   ")
    assert e.value.status_code == 400

    with pytest.raises(HTTPException) as e:
        category_service.create_category("x" * 21)
    assert e.value.status_code == 400

    category_service.create_category("餐饮2")
    with pytest.raises(HTTPException) as e:
        category_service.create_category("餐饮2")
    assert e.value.status_code == 400


def test_get_category_bill_count_scoped_by_user(db):
    from app.db.dao.category_dao import CategoryDAO

    cat = category_service.create_category("统计分类")
    BillDAO.insert_many(make_bill_records(3, category="统计分类"), USER_A)
    BillDAO.insert_many(make_bill_records(2, prefix="TB", category="统计分类"), USER_B)

    assert category_service.get_category(cat["id"], USER_A)["bill_count"] == 3
    assert category_service.get_category(cat["id"], USER_B)["bill_count"] == 2
    assert category_service.get_category(cat["id"], None)["bill_count"] == 5

    with pytest.raises(HTTPException) as e:
        category_service.get_category(99999, USER_A)
    assert e.value.status_code == 404


def test_update_rename_syncs_bills(db):
    from app.db.dao.category_dao import CategoryDAO

    cat = category_service.create_category("旧名")
    BillDAO.insert_many(make_bill_records(2, category="旧名"), USER_A)

    result = category_service.update_category(cat["id"], " 新名 ")
    assert result == {"id": cat["id"], "name": "新名", "renamed_bills": 2}
    assert CategoryDAO.get_by_name("旧名") is None
    total, _ = BillDAO.list_bills(USER_A, category="新名")
    assert total == 2


def test_update_same_name_noop(db):
    cat = category_service.create_category("原样")
    result = category_service.update_category(cat["id"], "原样")
    assert result["renamed_bills"] == 0


def test_update_validations(db):
    from app.db.dao.category_dao import CategoryDAO

    cat = category_service.create_category("临时")

    with pytest.raises(HTTPException):
        category_service.update_category(cat["id"], "  ")
    with pytest.raises(HTTPException):
        category_service.update_category(cat["id"], "x" * 21)
    with pytest.raises(HTTPException) as e:
        category_service.update_category(99999, "改名")
    assert e.value.status_code == 404

    other = category_service.create_category("已占用")
    with pytest.raises(HTTPException) as e:
        category_service.update_category(cat["id"], "已占用")
    assert e.value.status_code == 400
    _ = other

    # 默认分类不可改名
    default = CategoryDAO.get_by_name("其他")
    with pytest.raises(HTTPException) as e:
        category_service.update_category(default["id"], "别的")
    assert "不可重命名" in e.value.detail


def test_delete_moves_bills_to_default(db):
    cat = category_service.create_category("待删除")
    BillDAO.insert_many(make_bill_records(3, category="待删除"), USER_A)

    result = category_service.delete_category(cat["id"])
    assert result == {"id": cat["id"], "name": "待删除", "moved_bills": 3}
    total, _ = BillDAO.list_bills(USER_A, category="其他")
    assert total == 3


def test_delete_validations(db):
    from app.db.dao.category_dao import CategoryDAO

    with pytest.raises(HTTPException) as e:
        category_service.delete_category(99999)
    assert e.value.status_code == 404

    default = CategoryDAO.get_by_name("其他")
    with pytest.raises(HTTPException) as e:
        category_service.delete_category(default["id"])
    assert "不可删除" in e.value.detail
