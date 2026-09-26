"""分类关键词服务测试：清洗规则、匹配链第二层、表故障回退

注意：`db` 夹具已播种内置 RULES 词，测试一律用「喵喵」系虚构词避免撞车
（「喵喵」「喵喵茶」等不在 RULES 内；含「火锅」等真实词的断言需保证长词优先
仍由测试词胜出）。
"""

import pytest

from app.config import DEFAULT_CATEGORY
from app.db.dao.category_dao import CategoryDAO
from app.db.dao.category_keyword_dao import CategoryKeywordDAO
from app.services import keyword_service, learned_rule_service


def _add(category_name, words, source="manual"):
    return CategoryKeywordDAO.create_many(
        CategoryDAO.get_by_name(category_name)["id"], words, source
    )


# ---- clean_keywords ----


def test_clean_keywords_bounds_and_chars():
    valid, dropped = keyword_service.clean_keywords(
        ["喵喵", " a ", "a", "", "含 空格", "x" * 65, "12306"]
    )
    # 「 a 」strip 后为 "a" 过短被剔；「含 空格」含空白被剔；超长被剔；
    # 纯数字词合法（内置 RULES 里就有 "12306"）
    assert valid == ["喵喵", "12306"]
    assert dropped == 5


def test_clean_keywords_dedupes_case_insensitive():
    valid, _ = keyword_service.clean_keywords(["MiaoMiao", "miaomiao"])
    assert valid == ["MiaoMiao"]


def test_add_keywords_rejects_when_nothing_valid(db):
    from app.core.errors import ValidationError

    cat_id = CategoryDAO.get_by_name("餐饮")["id"]
    # 全部非法（长度 < 2）整体拒绝
    with pytest.raises(ValidationError):
        keyword_service.add_keywords(cat_id, ["a", ""])
    # 混合输入：合法词入库、非法词计数
    result = keyword_service.add_keywords(cat_id, ["喵喵", "a"])
    assert result["added"] == 1 and result["dropped"] == 1


def test_add_keywords_reports_duplicate_counts(db):
    cat_id = CategoryDAO.get_by_name("餐饮")["id"]
    first = keyword_service.add_keywords(cat_id, ["喵喵", "喵喵火锅城"])
    assert first == {"added": 2, "dropped": 0, "duplicated": 0}
    second = keyword_service.add_keywords(cat_id, ["喵喵", "喵喵火锅城"])
    assert second == {"added": 0, "dropped": 0, "duplicated": 2}


# ---- 匹配（第二层）----


def test_match_longest_keyword_wins(db):
    _add("餐饮", ["喵喵火锅城"])
    _add("购物", ["喵喵商城", "喵喵"])
    assert keyword_service.match("喵喵火锅城（分店）") == "餐饮"  # 5 字 > 2 字
    assert keyword_service.match("喵喵商城旗舰店") == "购物"  # 4 字 > 2 字
    assert keyword_service.match("喵喵优选") == "购物"  # 只命中宽泛词


def test_match_case_insensitive_and_remark(db):
    _add("数码", ["MiaoMiao"])
    _add("娱乐", ["喵喵茶"])
    assert keyword_service.match("miaomiao 会员店") == "数码"
    # 备注参与匹配（与被替代的 category_matcher 口径一致）
    assert keyword_service.match("无名商户", "买了喵喵茶") == "娱乐"
    assert keyword_service.match("", "") == DEFAULT_CATEGORY


def test_disabled_keyword_not_matched(db):
    CategoryDAO.ensure_many(["测试分类"])
    CategoryKeywordDAO.create_many(
        CategoryDAO.get_by_name("测试分类")["id"], ["量子科技"], "manual"
    )
    assert keyword_service.match("量子科技大厦") == "测试分类"
    kw = CategoryKeywordDAO.list_by_category(CategoryDAO.get_by_name("测试分类")["id"])[
        0
    ]
    CategoryKeywordDAO.set_enabled(kw["id"], False)
    assert keyword_service.match("量子科技大厦") == DEFAULT_CATEGORY


def test_match_falls_back_to_rules_when_table_broken(db, monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(
        CategoryKeywordDAO, "list_enabled_with_category", staticmethod(_boom)
    )
    # 表故障回退内置 RULES：瑞幸在 RULES["餐饮"] 中
    assert keyword_service.match("瑞幸咖啡") == "餐饮"


def test_apply_to_records_only_fills_empty(db):
    CategoryDAO.ensure_many(["测试分类"])
    CategoryKeywordDAO.create_many(
        CategoryDAO.get_by_name("测试分类")["id"], ["量子科技"], "manual"
    )
    records = [
        {"merchant": "量子科技大厦", "remark": "", "category": ""},
        {"merchant": "量子科技大厦", "remark": "", "category": "购物"},  # 已有分类不动
        {"merchant": "未知商户", "remark": "", "category": ""},
    ]
    matched = keyword_service.apply_to_records(records)
    assert matched == 1
    assert records[0]["category"] == "测试分类"
    assert records[1]["category"] == "购物"
    assert records[2]["category"] == DEFAULT_CATEGORY


def test_learned_rule_beats_keyword_in_import_chain(db):
    """导入链优先级：已学习规则 > 关键词表（链路顺序由 import_service 保证）"""
    # 用户两次把「量子科技大厦」纠正到「餐饮」→ 学习规则达标
    learned_rule_service.record_correction("量子科技大厦", "餐饮")
    learned_rule_service.record_correction("量子科技大厦", "餐饮")
    CategoryDAO.ensure_many(["测试分类"])
    CategoryKeywordDAO.create_many(
        CategoryDAO.get_by_name("测试分类")["id"], ["量子科技"], "manual"
    )
    records = [{"merchant": "量子科技大厦", "remark": "", "category": ""}]
    assert learned_rule_service.apply_to_records(records) == 1
    assert records[0]["category"] == "餐饮"  # 规则先行命中
    assert keyword_service.apply_to_records(records) == 0  # 关键词层不再改写


def test_empty_keyword_table_degrades_to_default(db):
    """空关键词表不崩：全部落「其他」（内置词全被停用后的自愈形态）"""
    for row in CategoryKeywordDAO.list_enabled_with_category():
        CategoryKeywordDAO.delete(row["id"])
    records = [{"merchant": "瑞幸咖啡", "remark": "", "category": ""}]
    assert keyword_service.apply_to_records(records) == 0
    assert records[0]["category"] == DEFAULT_CATEGORY


def test_add_keywords_unknown_category(db):
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        keyword_service.add_keywords(99999, ["喵喵"])
