"""API 路由集成测试（含飞牛网关身份头带来的账号隔离）"""

import io

from openpyxl import Workbook

from app.db.dao.bill_dao import BillDAO
from tests.conftest import USER_A, USER_B, make_bill_records
from conftest import assert_report

A_HEADERS = {
    "X-Trim-Userid": USER_A,
    "X-Trim-Username": "zhangsan",
    "X-Trim-Isadmin": "true",
}
B_HEADERS = {"X-Trim-Userid": USER_B}


def build_xlsx_bytes(rows: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


WECHAT_HEADER = [
    "交易时间",
    "交易类型",
    "交易对方",
    "商品",
    "收/支",
    "金额(元)",
    "支付方式",
    "当前状态",
    "交易单号",
    "商户单号",
    "备注",
]


# ---------- 账单导入 ----------


def test_upload_wechat_imports_and_auto_categorizes(client):
    content = build_xlsx_bytes(
        [
            ["说明行"],
            WECHAT_HEADER,
            [
                "2024-01-01 08:30:00",
                "商户消费",
                "瑞幸咖啡",
                "拿铁",
                "支出",
                "¥9.90",
                "零钱",
                "支付成功",
                "A1",
                "",
                "",
            ],
            [
                "2024-01-02 08:30:00",
                "商户消费",
                "滴滴出行",
                "快车",
                "支出",
                "¥15.00",
                "零钱",
                "支付成功",
                "A2",
                "",
                "",
            ],
        ]
    )
    resp = client.post(
        "/api/upload/wechat",
        files={"file": ("bill.xlsx", content, "application/octet-stream")},
        headers=A_HEADERS,
    )
    assert resp.status_code == 200
    assert_report(resp.json()["data"], total=2, inserted=2)

    _, rows = BillDAO.list_bills(USER_A)
    categories = {r["merchant"]: r["category"] for r in rows}
    assert categories["瑞幸咖啡"] == "餐饮"
    assert categories["滴滴出行"] == "交通"


def test_upload_duplicate_import_all_skipped(client):
    content = build_xlsx_bytes(
        [
            WECHAT_HEADER,
            [
                "2024-01-01 08:30:00",
                "商户消费",
                "瑞幸咖啡",
                "拿铁",
                "支出",
                "¥9.90",
                "零钱",
                "支付成功",
                "D1",
                "",
                "",
            ],
        ]
    )
    files = {"file": ("bill.xlsx", content, "application/octet-stream")}
    assert (
        client.post("/api/upload/wechat", files=files, headers=A_HEADERS).json()[
            "data"
        ]["inserted"]
        == 1
    )
    resp = client.post("/api/upload/wechat", files=files, headers=A_HEADERS)
    assert resp.json()["data"]["total"] == 1 and resp.json()["data"]["skipped_dup"] == 1
    assert len(resp.json()["data"]["details"]) == 1  # 差异报告：重复条目带逐条原因
    assert "已存在" in resp.json()["data"]["details"][0]["reason"]


def test_export_details_csv(client):
    """REQ-ING-005：导出导入差异报告为 CSV"""
    # 构造一份差异明细（含 CSV 注入试探的 reason）
    details = [
        {
            "tx_id": "T001",
            "merchant": "瑞幸咖啡",
            "amount": 9.9,
            "reason": "交易号已存在，重复跳过",
        },
        {
            "tx_id": "T002",
            "merchant": "测试商户",
            "amount": -5.0,
            "reason": "=EVIL",
        },
    ]
    resp = client.post(
        "/api/upload/export-details", json={"details": details}, headers=A_HEADERS
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    # utf-8-sig BOM
    assert resp.content[:3] == b"\xef\xbb\xbf"
    text = resp.content.decode("utf-8-sig")
    # 表头
    assert (
        "交易单号" in text and "商户" in text and "金额(元)" in text and "原因" in text
    )
    # 行数 = 表头 + 2 条明细
    assert len(text.strip().splitlines()) == 3
    # CSV 注入防护：=EVIL 前缀单引号
    assert "'=EVIL" in text


def test_upload_wechat_rejects_wrong_extension(client):
    resp = client.post(
        "/api/upload/wechat",
        files={"file": ("bill.csv", b"x", "text/csv")},
        headers=A_HEADERS,
    )
    assert resp.status_code == 400
    assert "仅支持" in resp.json()["msg"]


def test_upload_wechat_rejects_empty_file(client):
    resp = client.post(
        "/api/upload/wechat",
        files={"file": ("empty.xlsx", b"", "application/octet-stream")},
        headers=A_HEADERS,
    )
    assert resp.status_code == 400
    assert resp.json()["msg"] == "文件内容为空"


def test_upload_wechat_valid_xlsx_without_data_rows(client):
    """合法 xlsx 但没有任何流水行：导入成功且全部计数为 0"""
    content = build_xlsx_bytes([WECHAT_HEADER])
    resp = client.post(
        "/api/upload/wechat",
        files={"file": ("bill.xlsx", content, "application/octet-stream")},
        headers=A_HEADERS,
    )
    assert resp.status_code == 200
    assert_report(resp.json()["data"])


def test_upload_alipay_csv(client):
    header = (
        "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,"
        "收/付款方式,交易状态,交易订单号,商家订单号,备注"
    )
    content = (
        "支付宝账单说明行\r\n"
        f"{header}\r\n"
        "2024-01-01 08:30:00,餐饮美食,肯德基,,午餐,支出,45.00,余额,交易成功,ALI1,,\r\n"
        "2024-01-02 08:30:00,交通,滴滴,,快车,支出,15.50,余额,交易关闭,ALI2,,\r\n"
    ).encode("gb18030")
    resp = client.post(
        "/api/upload/alipay",
        files={"file": ("bill.csv", content, "text/csv")},
        headers=A_HEADERS,
    )
    assert resp.status_code == 200
    # 「交易关闭」流水在解析阶段就被剔除，不进入 total/skipped 统计
    assert_report(resp.json()["data"], total=1, inserted=1)
    _, rows = BillDAO.list_bills(USER_A, account="alipay")
    assert rows[0]["merchant"] == "肯德基"


# ---------- 流水管理 ----------


def test_bill_crud_with_user_isolation(client):
    payload = {
        "tx_time": "2024-03-01 10:00:00",
        "account": "wechat",
        "tx_type": "expense",
        "merchant": "便利店",
        "amount": 12.5,
        "category": "",
        "tx_id": "CRUD1",
        "remark": "",
    }
    created = client.post("/api/bill", json=payload, headers=A_HEADERS)
    assert created.status_code == 201
    bill = created.json()["data"]
    assert (
        bill["category"] == "其他" and "user_id" not in bill
    )  # user_id 不出现在响应模型
    bill_id = bill["id"]

    # B 账号看不到也改不了 A 的账单
    assert client.get(f"/api/bill/{bill_id}", headers=B_HEADERS).status_code == 404
    assert (
        client.put(
            f"/api/bill/{bill_id}", json={"remark": "偷改"}, headers=B_HEADERS
        ).status_code
        == 404
    )
    assert client.delete(f"/api/bill/{bill_id}", headers=B_HEADERS).status_code == 404

    updated = client.put(
        f"/api/bill/{bill_id}", json={"amount": 20, "remark": "改"}, headers=A_HEADERS
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["amount"] == 20.0

    listing = client.get(
        "/api/bill/list",
        params={"category": "其他", "sort_by": "amount", "order": "asc"},
        headers=A_HEADERS,
    ).json()["data"]
    assert listing["total"] == 1 and listing["items"][0]["id"] == bill_id
    assert listing["page"] == 1 and listing["page_size"] == 20

    assert client.delete(f"/api/bill/{bill_id}", headers=A_HEADERS).status_code == 204
    assert client.get(f"/api/bill/{bill_id}", headers=A_HEADERS).status_code == 404


def test_bill_list_rejects_invalid_sort_field(client):
    resp = client.get("/api/bill/list", params={"sort_by": "evil"}, headers=A_HEADERS)
    assert resp.status_code == 400


def test_bill_create_rejects_invalid_enum(client):
    resp = client.post(
        "/api/bill",
        json={
            "tx_time": "2024-03-01",
            "account": "bank",
            "tx_type": "expense",
            "amount": 1,
        },
        headers=A_HEADERS,
    )
    assert resp.status_code == 422  # pydantic Literal 校验


def test_bill_create_rejects_duplicate_tx_id(client):
    payload = {
        "tx_time": "2024-03-01 10:00:00",
        "account": "wechat",
        "tx_type": "expense",
        "amount": 5,
        "tx_id": "DUPX",
    }
    assert client.post("/api/bill", json=payload, headers=A_HEADERS).status_code == 201
    resp = client.post("/api/bill", json=payload, headers=B_HEADERS)
    assert resp.status_code == 400  # tx_id 全局唯一，跨账号也拦截


# ---------- 分类管理 ----------


def test_category_flow(client):
    created = client.post("/api/category", json={"name": "奶茶啡"})
    assert created.status_code == 201
    cat_id = created.json()["data"]["id"]

    assert client.post("/api/category", json={"name": "奶茶啡"}).status_code == 400
    assert client.post("/api/category", json={"name": "  "}).status_code == 400

    BillDAO.insert_many(make_bill_records(2, category="奶茶啡"), USER_A)
    detail = client.get(f"/api/category/{cat_id}", headers=A_HEADERS).json()["data"]
    assert detail["bill_count"] == 2
    assert (
        client.get(f"/api/category/{cat_id}", headers=B_HEADERS).json()["data"][
            "bill_count"
        ]
        == 0
    )

    renamed = client.put(f"/api/category/{cat_id}", json={"name": "奶茶"}).json()[
        "data"
    ]
    assert renamed["renamed_bills"] == 2

    deleted = client.delete(f"/api/category/{cat_id}").json()["data"]
    assert deleted["moved_bills"] == 2
    _, rows = BillDAO.list_bills(USER_A, category="其他")
    assert len(rows) == 2


def test_category_protects_default(client):
    categories = client.get("/api/category").json()["data"]
    default = next(c for c in categories if c["name"] == "其他")
    assert (
        client.put(f"/api/category/{default['id']}", json={"name": "改"}).status_code
        == 400
    )
    assert client.delete(f"/api/category/{default['id']}").status_code == 400


def test_category_write_requires_admin(client):
    """分类全局共享，写操作限管理员（防回归：普通账号不得增改删全局分类）

    无身份头的请求按「单机唯一用户」放行（独立部署 / 本地运行场景），因此这里
    用网关模式下的普通账号头验证 —— B_HEADERS 有 user_id 但不是管理员。
    """
    cat_id = client.post("/api/category", json={"name": "越权测试"}).json()["data"][
        "id"
    ]
    assert (
        client.post(
            "/api/category", json={"name": "普通账号建的"}, headers=B_HEADERS
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/category/{cat_id}", json={"name": "被改了"}, headers=B_HEADERS
        ).status_code
        == 403
    )
    assert (
        client.delete(f"/api/category/{cat_id}", headers=B_HEADERS).status_code == 403
    )
    # 管理员不受影响
    assert (
        client.delete(f"/api/category/{cat_id}", headers=A_HEADERS).status_code == 200
    )


# ---------- 统计报表 ----------


def test_stat_endpoints_scoped_by_user(client):
    BillDAO.insert_many(make_bill_records(2, amount=10.0, category="餐饮"), USER_A)
    BillDAO.insert_many(make_bill_records(1, amount=500.0, tx_id="B-ONLY"), USER_B)

    summary = client.get("/api/stat/summary", headers=A_HEADERS).json()["data"]
    assert summary == {"income": 0.0, "expense": 20.0, "net": -20.0}

    trend = client.get("/api/stat/month_trend", headers=A_HEADERS).json()["data"]
    assert trend == [{"month": "2024-01", "income": 0.0, "expense": 20.0}]

    pie = client.get("/api/stat/category_pie", headers=A_HEADERS).json()["data"]
    assert pie == [{"name": "餐饮", "value": 20.0}]

    top = client.get("/api/stat/merchant_top", headers=A_HEADERS).json()["data"]
    assert top[0]["merchant"] == "测试商户" and top[0]["count"] == 2

    # B 账号统计与 A 完全隔离
    assert (
        client.get("/api/stat/summary", headers=B_HEADERS).json()["data"]["expense"]
        == 500.0
    )


def test_stat_month_trend_accepts_date_filters(client):
    resp = client.get(
        "/api/stat/summary",
        params={"start": "2024-01-01", "end": "2024-01-31"},
        headers=A_HEADERS,
    )
    assert resp.status_code == 200


# ---------- 应用设置 ----------


def test_settings_database_info_and_claim(client):
    info = client.get("/api/settings/database", headers=A_HEADERS).json()["data"]
    assert info["db_type"] == "sqlite"
    assert info["user_id"] == USER_A
    assert info["user_name"] == "zhangsan"

    BillDAO.insert_many(make_bill_records(2), "")  # 历史无归属数据
    claimed = client.post("/api/settings/user/claim", headers=A_HEADERS).json()["data"]
    assert claimed["claimed"] == 2
    # 认领把全部无归属流水归到操作者名下、影响他人数据：收紧为管理员（评审 M-7）
    assert client.post("/api/settings/user/claim", headers=B_HEADERS).status_code == 403

    info = client.get("/api/settings/database", headers=A_HEADERS).json()["data"]
    assert info["unassigned_bills"] == 0

    # 非管理员脱敏：服务器路径/连接信息/待认领数量不回传（评审 M-5）
    info_b = client.get("/api/settings/database", headers=B_HEADERS).json()["data"]
    assert info_b["sqlite_path"] is None
    assert info_b["host"] is None
    assert info_b["unassigned_bills"] == 0


def test_request_without_gateway_headers_uses_default_account(client):
    """本地/独立部署无网关头：归入空串默认账号"""
    payload = {
        "tx_time": "2024-03-01 10:00:00",
        "account": "wechat",
        "tx_type": "expense",
        "amount": 5,
        "tx_id": "LOCAL1",
    }
    assert client.post("/api/bill", json=payload).status_code == 201
    assert BillDAO.list_bills("")[0] == 1
    # 有网关头的账号看不到默认账号的数据
    assert client.get("/api/bill/list", headers=A_HEADERS).json()["data"]["total"] == 0


def test_bill_list_multi_value_filters(client):
    """多分类（IN）+ 多商户（OR 子串）筛选：T-6.2「存为筛选」的口径复现

    问账口径（categories/merchants）与流水页筛选语义一致，
    才能保证"跳转流水页复现同样的筛选"不是近似。
    """
    from tests.conftest import make_bill_records
    from app.db.dao.bill_dao import BillDAO

    rows = []
    for i, (cat, merchant, amount) in enumerate(
        [
            ("餐饮", "美团外卖", 30.0),
            ("交通", "美团打车", 20.0),
            ("购物", "京东商城", 100.0),
            ("餐饮", "肯德基", 40.0),
        ]
    ):
        rows.extend(
            make_bill_records(
                1,
                prefix=f"MVF{i}",
                tx_time="2026-08-10 12:00:00",
                category=cat,
                merchant=merchant,
                amount=amount,
            )
        )
    BillDAO.insert_many(rows, USER_A)

    def _total(**params):
        return client.get("/api/bill/list", params=params, headers=A_HEADERS).json()[
            "data"
        ]

    # 多分类 IN：餐饮+交通 = 3 条
    data = _total(categories=["餐饮", "交通"])
    assert data["total"] == 3
    assert {i["category"] for i in data["items"]} == {"餐饮", "交通"}

    # 多商户 OR 子串：美团（外卖+打车两笔）+ 肯德基 = 3 条
    data = _total(merchants=["美团", "肯德基"])
    assert data["total"] == 3

    # 组合：分类(餐饮) + 商户(美团) → 仅美团外卖
    data = _total(categories=["餐饮"], merchants=["美团"])
    assert data["total"] == 1
    assert data["items"][0]["merchant"] == "美团外卖"

    # 与单值 category 参数并存互不干扰：category=购物 → 1 条
    assert _total(category="购物")["total"] == 1

    # 导出端点同样接受多值筛选（与列表同口径）
    resp = client.get(
        "/api/bill/export",
        params={"categories": ["餐饮"], "merchants": ["美团"], "format": "csv"},
        headers=A_HEADERS,
    )
    assert resp.status_code == 200
    assert "美团外卖" in resp.content.decode("utf-8")
