"""消费分类 DAO 测试"""
from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from tests.conftest import USER_A, make_bill_records


def test_list_all_and_get(db):
    names = [c["name"] for c in CategoryDAO.list_all()]
    assert "餐饮" in names and "其他" in names
    cat_id = CategoryDAO.get_by_name("餐饮")["id"]
    assert CategoryDAO.get_by_id(cat_id)["name"] == "餐饮"
    assert CategoryDAO.get_by_id(99999) is None
    assert CategoryDAO.get_by_name("不存在") is None


def test_create_duplicate_returns_none(db):
    assert CategoryDAO.create("数码配件") is not None
    assert CategoryDAO.create("数码配件") is None


def test_ensure_many_idempotent(db):
    before = len(CategoryDAO.list_all())
    assert CategoryDAO.ensure_many(["餐饮", "新分类1", "新分类2"]) == 2
    assert CategoryDAO.ensure_many(["餐饮", "新分类1"]) == 0
    assert len(CategoryDAO.list_all()) == before + 2
    assert CategoryDAO.ensure_many([]) == 0
    assert CategoryDAO.ensure_many(["", "   "]) == 0  # 空白名被过滤


def test_rename_syncs_bills(db):
    CategoryDAO.ensure_many(["宠物用品"])
    BillDAO.insert_many(make_bill_records(3, category="宠物用品"), USER_A)
    BillDAO.insert_many(make_bill_records(2, prefix="T9", category="宠物用品"), "")

    renamed = CategoryDAO.rename(CategoryDAO.get_by_name("宠物用品")["id"], "宠物百货")
    assert renamed == 5  # 流水同步更新，不分账号
    assert CategoryDAO.get_by_name("宠物用品") is None
    assert CategoryDAO.get_by_name("宠物百货") is not None


def test_delete_moves_bills_to_fallback(db):
    CategoryDAO.ensure_many(["临时分类"])
    BillDAO.insert_many(make_bill_records(2, category="临时分类"), USER_A)
    cat_id = CategoryDAO.get_by_name("临时分类")["id"]

    moved = CategoryDAO.delete(cat_id, fallback="其他")
    assert moved == 2
    assert CategoryDAO.get_by_id(cat_id) is None
    total, rows = BillDAO.list_bills(USER_A, category="其他")
    assert (total, len(rows)) == (2, 2)


def test_repair_orphans(db):
    """直接操作数据库删分类产生孤儿流水后，可一键归入兜底分类"""
    CategoryDAO.ensure_many(["孤儿分类"])
    BillDAO.insert_many(make_bill_records(2, category="孤儿分类"), USER_A)
    # 模拟绕过应用直接删除分类行
    from app.db.base import get_db
    from sqlalchemy import delete
    from app.db.models import Category

    with get_db() as session:
        session.execute(delete(Category).where(Category.name == "孤儿分类"))

    assert CategoryDAO.repair_orphans() == 2
    total, _ = BillDAO.list_bills(USER_A, category="其他")
    assert total == 2
