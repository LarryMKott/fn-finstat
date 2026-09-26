"""备份 v7（分类层级 + category_keywords）导出/恢复测试

覆盖方案 §12「备份导出扩舱」底线：
- v7 备份 roundtrip：层级按 parent_name 重映射、关键词按分类名重映射
- v6 老格式备份 replace 恢复：关键词表被清空后自动重播种内置词
"""

from app.db.dao.bill_dao import BillDAO
from app.db.dao.category_dao import CategoryDAO
from app.db.dao.category_keyword_dao import CategoryKeywordDAO
from app.services import backup_service
from app.services.keyword_service import match
from tests.conftest import USER_A, make_bill_records


def _keyword_words(category_name):
    return {
        r["keyword"]
        for r in CategoryKeywordDAO.list_by_category(
            CategoryDAO.get_by_name(category_name)["id"]
        )
    }


def test_v7_roundtrip_preserves_hierarchy_and_keywords(db):
    food_id = CategoryDAO.get_by_name("餐饮")["id"]
    child_id = CategoryDAO.create("喵喵烘焙", parent_id=food_id, source="ai")
    assert child_id is not None
    CategoryKeywordDAO.create_many(child_id, ["喵喵包"], "ai")
    BillDAO.insert_many(
        make_bill_records(1, merchant="喵喵烘焙坊", category="餐饮"), USER_A
    )

    data = backup_service.export_backup()
    cat_row = next(c for c in data["categories"] if c["name"] == "喵喵烘焙")
    assert cat_row["parent_name"] == "餐饮"  # 层级按名导出
    kw_row = next(k for k in data["category_keywords"] if k["keyword"] == "喵喵包")
    assert kw_row["category_name"] == "喵喵烘焙"
    assert data["format_version"] == backup_service.BACKUP_FORMAT_VERSION

    # replace 恢复到同一库（先清空再灌回，id 会重排——按名对齐）
    result = backup_service.restore_backup(data, replace=True)
    assert result["category_keywords"] >= 1
    restored_child = CategoryDAO.get_by_name("喵喵烘焙")
    assert restored_child is not None
    assert restored_child["parent_id"] == CategoryDAO.get_by_name("餐饮")["id"]
    assert restored_child["source"] == "ai"
    assert "喵喵包" in _keyword_words("喵喵烘焙")


def test_v6_backup_replace_reseeds_builtin_keywords(db):
    """老格式（v6，无关键词节）replace 恢复：重播种内置词，匹配链不断供"""
    data = {
        "app": "fn-finstat",
        "format_version": 6,
        "categories": ["餐饮", "交通"],
        "bills": [],
    }
    result = backup_service.restore_backup(data, replace=True)
    assert result["categories"] == 2
    # v6 时代没有「喵喵」系测试词，但内置 RULES 的「瑞幸」必须回来
    assert "瑞幸" in _keyword_words("餐饮")
    assert match("瑞幸咖啡（朝阳门店）") == "餐饮"


def test_v7_backup_keyword_orphan_counted(db):
    data = {
        "app": "fn-finstat",
        "format_version": 7,
        "categories": ["餐饮"],
        "category_keywords": [
            {
                "category_name": "餐饮",
                "keyword": "喵喵词",
                "source": "ai",
                "enabled": True,
            },
            {
                "category_name": "不存在的分类",
                "keyword": "孤儿词",
                "source": "ai",
                "enabled": True,
            },
        ],
        "bills": [],
    }
    result = backup_service.restore_backup(data, replace=True)
    assert result["category_keywords"] == 1
    assert result["skipped"] >= 1  # 孤儿关键词按坏行计数
    assert "喵喵词" in _keyword_words("餐饮")


def test_old_v6_string_categories_still_accepted(db):
    """v2 及更早的纯字符串分类数组兼容不被 v7 改动破坏"""
    data = {
        "app": "fn-finstat",
        "format_version": 2,
        "categories": ["字符串分类"],
        "bills": [],
    }
    result = backup_service.restore_backup(data, replace=True)
    assert result["categories"] == 1
    assert CategoryDAO.get_by_name("字符串分类") is not None
