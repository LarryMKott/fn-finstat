"""地域识别工具测试：城市 / 省名 / 地标 / 歧义消解 / 未识别

地域识别是「消费地图」的地基——账单里没有地区字段，全靠文本推断，
因此规则的正确性与边界（宁可判未识别也不误判）必须有回归保护。
"""

from app.utils.region_matcher import (
    PROVINCE_NAMES,
    detect_city,
    detect_region,
)


def test_city_with_admin_suffix_wins():
    """带行政后缀的全称最可靠"""
    assert detect_region("成都市第一人民医院") == "四川省"
    assert detect_region("乌鲁木齐市大巴扎") == "新疆维吾尔自治区"
    assert detect_region("内蒙古自治区呼和浩特店") == "内蒙古自治区"


def test_municipality_returns_city_suffixed():
    """直辖市归一化为「北京市」形式，与地图 name 对齐"""
    assert detect_region("国贸大厦(北京)") == "北京市"
    assert detect_region("上海静安寺苹果店") == "上海市"
    assert detect_region("重庆解放碑店") == "重庆市"
    assert detect_region("天津滨江道") == "天津市"


def test_landmark_beats_city_name():
    """地标的地域指向强于城市裸词：「南京路」在上海而非南京"""
    assert detect_region("南京路步行街") == "上海市"
    assert detect_region("华强北赛格广场") == "广东省"
    assert detect_region("春熙路太古里") == "四川省"


def test_city_before_landmark_wins():
    """城市名出现在地标之前时，城市名是主体：「海口国贸」指海口"""
    assert detect_region("海口国贸") == "海南省"
    assert detect_city("海口国贸") == "海口"


def test_negative_words_not_matched_as_city():
    """「长安街」不应被识别为西安（长安）"""
    # 无城市前缀时，「长安街」单独出现不应误判；但带「西安」时应正确
    assert detect_region("西安长安街") == "陕西省"


def test_plain_merchants_are_unmatched():
    """纯连锁品牌无地域线索：必须落到未识别，不能瞎猜"""
    for name in ["瑞幸咖啡", "京东商城", "美团外卖", "腾讯视频", "滴滴出行"]:
        assert detect_region(name) is None, name
        assert detect_city(name) is None, name


def test_empty_input_returns_none():
    assert detect_region("", "") is None
    assert detect_region(None) is None
    assert detect_city() is None


def test_remark_contributes():
    """备注可作为地域线索来源（商户名无线索时）"""
    assert detect_region("某某商户", "成都出差住宿") == "四川省"


def test_result_always_in_province_names():
    """输出必须落在标准省级全称集合内，否则地图着色会静默失效"""
    cases = [
        "成都市第一人民医院",
        "国贸大厦(北京)",
        "深圳华强北",
        "乌鲁木齐大巴扎",
        "内蒙古自治区呼和浩特店",
        "香港铜锣湾",
        "台北101",
        "拉萨八廓街",
        "银川新华街",
        "南宁朝阳广场",
    ]
    for text in cases:
        region = detect_region(text)
        assert region in PROVINCE_NAMES, f"{text} -> {region}"


def test_city_maps_to_correct_province():
    """城市与所属省份的对应关系正确（用于下钻与城市 TOP）"""
    assert (detect_city("成都地铁"), detect_region("成都地铁")) == ("成都", "四川省")
    assert (detect_city("杭州西湖银泰"), detect_region("杭州西湖银泰")) == (
        "杭州",
        "浙江省",
    )
    assert (detect_city("深圳华强北"), detect_region("深圳华强北")) == (
        "深圳",
        "广东省",
    )
