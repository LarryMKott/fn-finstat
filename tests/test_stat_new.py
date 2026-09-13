"""新统计接口测试：日历热力图（按日汇总）与年度对比报表"""

from app.db.dao.bill_dao import BillDAO
from tests.conftest import USER_A, USER_B, make_bill_records

A_HEADERS = {"X-Trim-Userid": USER_A}


def seed_rows(rows, user_id=USER_A, prefix="ST"):
    records = []
    for i, (tx_time, tx_type, amount, category) in enumerate(rows):
        records.append(
            {
                "tx_time": tx_time,
                "account": "wechat",
                "tx_type": tx_type,
                "merchant": f"商户{i}",
                "amount": amount,
                "category": category,
                "tx_id": f"{prefix}-{i:04d}",
                "remark": "",
            }
        )
    BillDAO.insert_many(records, user_id)


def test_daily_heatmap_groups_by_day(client):
    seed_rows(
        [
            ("2026-09-01 08:00:00", "expense", 10.0, "餐饮"),
            ("2026-09-01 20:00:00", "expense", 5.5, "交通"),
            ("2026-09-02 12:00:00", "income", 100.0, "其他"),
            ("2026-08-31 23:00:00", "expense", 99.0, "购物"),
        ]
    )
    data = client.get(
        "/api/stat/daily_heatmap?year=2026&month=9", headers=A_HEADERS
    ).json()["data"]
    assert data == [
        {"date": "2026-09-01", "income": 0.0, "expense": 15.5},
        {"date": "2026-09-02", "income": 100.0, "expense": 0.0},
    ]
    # 不传月份 → 全年
    data = client.get("/api/stat/daily_heatmap?year=2026", headers=A_HEADERS).json()["data"]
    assert len(data) == 3


def test_daily_heatmap_scoped_by_user(client):
    seed_rows([("2026-09-01 08:00:00", "expense", 10.0, "餐饮")])
    other = BillDAO.insert_many(
        make_bill_records(1, prefix="STB", tx_time="2026-09-05 08:00:00"), USER_B
    )
    assert other == 1
    mine = client.get("/api/stat/daily_heatmap?year=2026", headers=A_HEADERS).json()["data"]
    assert all(d["date"] == "2026-09-01" for d in mine)


def test_year_comparison(client):
    seed_rows(
        [
            # 今年 1 月：支出 120，收入 0
            ("2026-01-15 10:00:00", "expense", 120.0, "餐饮"),
            # 去年 1 月：支出 100
            ("2025-01-15 10:00:00", "expense", 100.0, "餐饮"),
            # 去年 2 月：支出 50（今年 2 月没有）
            ("2025-02-15 10:00:00", "expense", 50.0, "交通"),
            # 区间外数据不参与
            ("2024-01-01 10:00:00", "expense", 999.0, "购物"),
        ]
    )
    data = client.get("/api/stat/year_comparison?year=2026", headers=A_HEADERS).json()["data"]
    assert data["year"] == 2026 and data["last_year"] == 2025
    assert data["this_expense"] == 120 and data["last_expense"] == 150
    monthly = {m["month"]: m for m in data["monthly"]}
    assert len(monthly) == 12
    assert monthly["01"]["this_expense"] == 120
    assert monthly["01"]["last_expense"] == 100
    assert monthly["02"]["last_expense"] == 50
    assert monthly["03"]["this_expense"] == 0
    cats = {c["category"]: c for c in data["categories"]}
    assert cats["餐饮"] == {"category": "餐饮", "this_year": 120.0, "last_year": 100.0}
    assert cats["交通"]["last_year"] == 50 and cats["交通"]["this_year"] == 0


def test_year_comparison_defaults_to_current_year(client):
    data = client.get("/api/stat/year_comparison", headers=A_HEADERS).json()["data"]
    assert data["year"] >= 2026
