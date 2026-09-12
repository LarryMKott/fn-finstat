"""统计服务/DAO 测试（含账号隔离与月度分组）"""
from app.db.dao.bill_dao import BillDAO
from app.services import stat_service
from tests.conftest import USER_A, USER_B


def seed(db):
    BillDAO.insert_many([
        # 1月：支出 100（餐饮 60 + 交通 40），收入 500
        {"tx_time": "2024-01-05 10:00:00", "account": "wechat", "tx_type": "expense",
         "merchant": "瑞幸咖啡", "amount": 60.0, "category": "餐饮", "tx_id": "S01", "remark": ""},
        {"tx_time": "2024-01-20 10:00:00", "account": "alipay", "tx_type": "expense",
         "merchant": "滴滴", "amount": 40.0, "category": "交通", "tx_id": "S02", "remark": ""},
        {"tx_time": "2024-01-25 10:00:00", "account": "alipay", "tx_type": "income",
         "merchant": "公司", "amount": 500.0, "category": "其他", "tx_id": "S03", "remark": ""},
        # 2月：支出 30（餐饮）
        {"tx_time": "2024-02-10 10:00:00", "account": "wechat", "tx_type": "expense",
         "merchant": "瑞幸咖啡", "amount": 30.0, "category": "餐饮", "tx_id": "S04", "remark": ""},
        # 转账不计收支
        {"tx_time": "2024-02-11 10:00:00", "account": "wechat", "tx_type": "transfer",
         "merchant": "", "amount": 900.0, "category": "其他", "tx_id": "S05", "remark": ""},
    ], USER_A)
    BillDAO.insert_many([
        {"tx_time": "2024-01-01 10:00:00", "account": "wechat", "tx_type": "expense",
         "merchant": "别人的", "amount": 777.0, "category": "购物", "tx_id": "S07", "remark": ""},
    ], USER_B)


def test_summary(db):
    seed(db)
    data = stat_service.summary(USER_A)
    assert data == {"income": 500.0, "expense": 130.0, "net": 370.0}

    data = stat_service.summary(USER_A, tx_type="expense")
    assert data == {"income": 0.0, "expense": 130.0, "net": -130.0}

    data = stat_service.summary(USER_A, account="alipay")
    assert data == {"income": 500.0, "expense": 40.0, "net": 460.0}

    data = stat_service.summary(USER_A, start="2024-02-01", end="2024-02-28")
    assert data == {"income": 0.0, "expense": 30.0, "net": -30.0}

    # 空库汇总为 0
    assert stat_service.summary(USER_B, start="2099-01-01") == {
        "income": 0.0, "expense": 0.0, "net": 0.0,
    }


def test_month_trend(db):
    seed(db)
    trend = stat_service.month_trend(USER_A)
    assert trend == [
        {"month": "2024-01", "income": 500.0, "expense": 100.0},
        {"month": "2024-02", "income": 0.0, "expense": 30.0},
    ]
    assert stat_service.month_trend(USER_B) == [
        {"month": "2024-01", "income": 0.0, "expense": 777.0},
    ]


def test_category_pie_orders_by_amount_desc(db):
    seed(db)
    pie = stat_service.category_pie(USER_A)
    assert pie == [
        {"name": "餐饮", "value": 90.0},
        {"name": "交通", "value": 40.0},
    ]
    # 只统计支出，指定月份后只剩 2 月数据
    assert stat_service.category_pie(USER_A, start="2024-02-01") == [
        {"name": "餐饮", "value": 30.0},
    ]


def test_merchant_top(db):
    seed(db)
    top = stat_service.merchant_top(USER_A)
    assert top == [
        {"merchant": "瑞幸咖啡", "amount": 90.0, "count": 2},
        {"merchant": "滴滴", "amount": 40.0, "count": 1},
    ]
    # limit 生效；空商户（转账）不参与排行
    assert stat_service.merchant_top(USER_A, limit=1) == [
        {"merchant": "瑞幸咖啡", "amount": 90.0, "count": 2},
    ]


def test_round2_handles_none():
    assert stat_service._round2(None) == 0.0
    assert stat_service._round2(1.005) == 1.0
    assert stat_service._round2("2.675") == 2.67
