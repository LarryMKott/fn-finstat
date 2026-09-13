"""消费地图接口测试：省级聚合、城市 TOP、识别率与账号隔离

重点验证「未识别」不被伪造为地域，以及识别率口径正确——这是该功能
向用户暴露局限性的关键字段。
"""

from app.db.dao.bill_dao import BillDAO
from tests.conftest import USER_A, USER_B

A_HEADERS = {"X-Trim-Userid": USER_A}
B_HEADERS = {"X-Trim-Userid": USER_B}


def seed_rows(rows, user_id=USER_A, prefix="RG"):
    """rows: (商户名, 备注, 金额, 收支类型)"""
    records = []
    for i, (merchant, remark, amount, tx_type) in enumerate(rows):
        records.append(
            {
                "tx_time": f"2026-09-{i + 1:02d} 12:00:00",
                "account": "wechat",
                "tx_type": tx_type,
                "merchant": merchant,
                "amount": amount,
                "category": "其他",
                "tx_id": f"{prefix}-{i:04d}",
                "remark": remark,
            }
        )
    BillDAO.insert_many(records, user_id)


def test_region_map_aggregates_by_province(client):
    seed_rows(
        [
            ("海底捞火锅(春熙路店)", "", 200.0, "expense"),
            ("成都市第一人民医院", "", 100.0, "expense"),
            ("国贸大厦(北京)", "", 500.0, "expense"),
        ]
    )
    data = client.get("/api/stat/region_map", headers=A_HEADERS).json()["data"]

    provinces = {p["name"]: p for p in data["provinces"]}
    assert provinces["四川省"]["value"] == 300.0
    assert provinces["四川省"]["count"] == 2
    assert provinces["北京市"]["value"] == 500.0
    # 省份按金额降序，最大值用于地图 visualMap 上限
    assert data["provinces"][0]["name"] == "北京市"
    assert data["max_value"] == 500.0


def test_region_map_excludes_income_and_transfer(client):
    """地图只看消费去向：收入与转账不参与"""
    seed_rows(
        [
            ("成都地铁", "", 10.0, "expense"),
            ("公司", "工资", 9999.0, "income"),
            ("张三", "借出", 500.0, "transfer"),
        ]
    )
    data = client.get("/api/stat/region_map", headers=A_HEADERS).json()["data"]
    assert data["total_amount"] == 10.0
    assert data["scanned_count"] == 1


def test_region_map_reports_unmatched_rate(client):
    """未识别部分如实计入分母，识别率反映真实覆盖率"""
    seed_rows(
        [
            ("成都地铁", "", 100.0, "expense"),
            ("瑞幸咖啡", "", 100.0, "expense"),  # 无地域线索
        ]
    )
    data = client.get("/api/stat/region_map", headers=A_HEADERS).json()["data"]
    assert data["total_amount"] == 200.0
    assert data["matched_amount"] == 100.0
    assert data["matched_count"] == 1
    assert data["matched_rate"] == 50
    assert len(data["provinces"]) == 1  # 未识别不生成伪省份


def test_region_map_empty_db(client):
    data = client.get("/api/stat/region_map", headers=A_HEADERS).json()["data"]
    assert data["provinces"] == []
    assert data["cities"] == []
    assert data["max_value"] == 0.0
    assert data["matched_rate"] == 0
    assert data["truncated"] is False


def test_region_map_city_top_sorted_and_scoped(client):
    seed_rows(
        [
            ("成都地铁", "", 50.0, "expense"),
            ("成都红旗连锁", "", 30.0, "expense"),
            ("杭州西湖银泰", "", 200.0, "expense"),
        ]
    )
    data = client.get("/api/stat/region_map", headers=A_HEADERS).json()["data"]
    cities = data["cities"]
    assert cities[0]["name"] == "杭州" and cities[0]["province"] == "浙江省"
    chengdu = next(c for c in cities if c["name"] == "成都")
    assert chengdu["value"] == 80.0 and chengdu["count"] == 2

    # B 账号看不到 A 的数据
    other = client.get("/api/stat/region_map", headers=B_HEADERS).json()["data"]
    assert other["provinces"] == []


def test_region_map_supports_date_range(client):
    seed_rows([("成都地铁", "", 10.0, "expense")])
    inside = client.get(
        "/api/stat/region_map?start=2026-09-01&end=2026-09-30", headers=A_HEADERS
    ).json()["data"]
    outside = client.get(
        "/api/stat/region_map?start=2026-01-01&end=2026-01-31", headers=A_HEADERS
    ).json()["data"]
    assert inside["provinces"] != []
    assert outside["provinces"] == []


def test_region_map_cities_carry_coordinates(client):
    """气泡图依赖城市经纬度：每个上榜城市都必须带合法坐标

    坐标落在 [经度 73~136, 纬度 3~54] 区间内即为中国境内，可防止
    坐标表错位（如把纬度当经度）导致气泡飘到地图外。
    """
    seed_rows(
        [
            ("成都地铁", "", 50.0, "expense"),
            ("杭州西湖银泰", "", 200.0, "expense"),
            ("深圳华强北", "", 80.0, "expense"),
        ]
    )
    data = client.get("/api/stat/region_map", headers=A_HEADERS).json()["data"]
    assert data["cities"]
    for c in data["cities"]:
        assert isinstance(c["coord"], list) and len(c["coord"]) == 2
        lng, lat = c["coord"]
        assert 73 <= lng <= 136, f"{c['name']} 经度异常：{lng}"
        assert 3 <= lat <= 54, f"{c['name']} 纬度异常：{lat}"

    chengdu = next(c for c in data["cities"] if c["name"] == "成都")
    assert chengdu["coord"] == [104.065735, 30.659462]
