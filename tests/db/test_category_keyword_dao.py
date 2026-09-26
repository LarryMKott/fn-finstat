"""分类关键词 DAO 测试：CRUD、唯一约束降级、同词跨分类、删除分类级联

注意：`db` 夹具已播种内置 RULES 词，测试一律用「喵喵」系虚构词避免撞车。
"""

from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from app.db.dao.category_keyword_dao import CategoryKeywordDAO
from tests.conftest import USER_A, make_bill_records


def test_create_many_and_list(db):
    cat_id = CategoryDAO.get_by_name("餐饮")["id"]
    added = CategoryKeywordDAO.create_many(
        cat_id, ["喵喵", "喵喵火锅城", "喵喵"], "manual"
    )
    assert added == 2  # 批内去重（保留首个写法）
    rows = CategoryKeywordDAO.list_by_category(cat_id)
    mine = {r["keyword"]: r for r in rows if r["keyword"].startswith("喵喵")}
    assert set(mine) == {"喵喵", "喵喵火锅城"}
    assert all(r["source"] == "manual" and r["enabled"] for r in mine.values())


def test_unique_conflict_skipped_silently(db):
    cat_id = CategoryDAO.get_by_name("餐饮")["id"]
    assert CategoryKeywordDAO.create_many(cat_id, ["喵喵"], "manual") == 1
    # 与既有词重复：跳过不报错（幂等）
    assert CategoryKeywordDAO.create_many(cat_id, ["喵喵"], "ai") == 0


def test_same_word_across_categories_allowed(db):
    food = CategoryDAO.get_by_name("餐饮")["id"]
    transport = CategoryDAO.get_by_name("交通")["id"]
    assert CategoryKeywordDAO.create_many(food, ["喵喵"], "manual") == 1
    assert CategoryKeywordDAO.create_many(transport, ["喵喵"], "manual") == 1
    food_words = {r["keyword"] for r in CategoryKeywordDAO.list_by_category(food)}
    transport_words = {
        r["keyword"] for r in CategoryKeywordDAO.list_by_category(transport)
    }
    assert "喵喵" in food_words and "喵喵" in transport_words


def test_set_enabled_and_delete(db):
    cat_id = CategoryDAO.get_by_name("餐饮")["id"]
    CategoryKeywordDAO.create_many(cat_id, ["喵喵"], "manual")
    rows = [
        r for r in CategoryKeywordDAO.list_by_category(cat_id) if r["keyword"] == "喵喵"
    ]
    kw = rows[0]
    updated = CategoryKeywordDAO.set_enabled(kw["id"], False)
    assert updated["enabled"] is False
    assert kw["id"] not in {
        r["id"] for r in CategoryKeywordDAO.list_by_category(cat_id, enabled_only=True)
    }
    assert CategoryKeywordDAO.delete(kw["id"]) is True
    assert CategoryKeywordDAO.delete(kw["id"]) is False
    assert CategoryKeywordDAO.get(kw["id"]) is None


def test_delete_category_cascades_keywords(db):
    CategoryDAO.ensure_many(["临时分类"])
    cat_id = CategoryDAO.get_by_name("临时分类")["id"]
    CategoryKeywordDAO.create_many(cat_id, ["临时词"], "manual")
    BillDAO.insert_many(make_bill_records(1, category="临时分类"), USER_A)

    CategoryDAO.delete(cat_id)
    assert CategoryKeywordDAO.list_by_category(cat_id) == []
    assert CategoryDAO.get_by_id(cat_id) is None


def test_list_enabled_with_category_sorts_longest_first(db):
    food = CategoryDAO.get_by_name("餐饮")["id"]
    transport = CategoryDAO.get_by_name("交通")["id"]
    CategoryKeywordDAO.create_many(food, ["喵喵火锅城", "喵喵"], "manual")
    CategoryKeywordDAO.create_many(transport, ["喵喵商城"], "manual")
    rows = CategoryKeywordDAO.list_enabled_with_category()
    mine = [r for r in rows if r["keyword"].startswith("喵喵")]
    keywords = [r["keyword"] for r in mine]
    assert keywords == ["喵喵火锅城", "喵喵商城", "喵喵"]  # 长词在前，同长按 id
    by_word = {r["keyword"]: r["category"] for r in mine}
    assert by_word["喵喵商城"] == "交通"
