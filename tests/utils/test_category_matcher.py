"""商户关键词自动归类测试"""

from app.utils.category_matcher import RULES, match_category


def test_each_category_has_a_hit():
    """每个分类至少有一条规则能命中自身"""
    samples = {
        "餐饮": "瑞幸咖啡",
        "交通": "滴滴出行",
        "购物": "京东商城",
        "住房": "本月房租",
        "医疗": "老百姓大药房",
        "娱乐": "爱奇艺会员",
        "数码": "小米手机",
        "通讯": "中国移动话费",
        "教育": "考研网课",
        "宠物": "皇家猫粮",
    }
    for category, merchant in samples.items():
        assert match_category(merchant) == category


def test_miss_returns_default():
    assert match_category("张三") == "其他"
    assert match_category("") == "其他"


def test_remark_participates_in_matching():
    assert match_category("张三", "备注：停车费") == "交通"


def test_case_insensitive_for_latin_keywords():
    assert match_category("STEAM 游戏充值") == "娱乐"
    assert match_category("COSTCO 超市") == "购物"
    assert match_category("KTV 欢唱") == "娱乐"


def test_first_rule_in_order_wins():
    """匹配按 RULES 字典顺序进行，首个命中即返回"""
    for category, keywords in RULES.items():
        assert match_category(keywords[0]) == category


def test_no_side_effect_between_calls():
    assert match_category("京东") == "购物"
    assert match_category("京东") == "购物"
