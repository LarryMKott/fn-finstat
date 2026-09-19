"""预算建议 + 现金流预测测试（T-6.4）：固定项识别、一次性大额剔除、
分位线计算、口径排除重算、起点余额口径、账号隔离与参数校验"""

from datetime import date, timedelta

import pytest

from app.core.errors import ValidationError
from app.db.dao.asset_dao import AssetDAO
from app.db.dao.bill_dao import BillDAO
from app.services import forecast_service
from app.services.forecast_service import budget_suggestions, cash_flow
from tests.conftest import USER_A, USER_B, make_bill_records

A_HEADERS = {"X-Trim-Userid": USER_A}
B_HEADERS = {"X-Trim-Userid": USER_B}

# 固定测试基准日：窗口 = 2026-06/07/08 三个完整月，今日为 2026-09-18
TODAY = date(2026, 9, 18)
WINDOW = ["2026-06", "2026-07", "2026-08"]

_seq = [0]


def add_bills(
    user_id,
    prefix,
    month_day,
    amount,
    *,
    merchant="商户",
    category="餐饮",
    tx_type="expense",
    count=1,
):
    """在指定月份某天插入 count 笔流水；prefix 需全局唯一（tx_id 去重）"""
    _seq[0] += 1
    return BillDAO.insert_many(
        make_bill_records(
            count,
            prefix=f"{prefix}{_seq[0]}",
            tx_time=f"{month_day} 12:00:00",
            merchant=merchant,
            category=category,
            tx_type=tx_type,
            amount=amount,
        ),
        user_id,
    )


def seed_fixed_history(user_id="A"):
    """标准样例：房租 3000（1 号）×3、工资 10000（5 号）×3、可变支出 900/1000/1100"""
    uid = USER_A if user_id == "A" else user_id
    for i, m in enumerate(WINDOW):
        add_bills(
            uid, f"rent{i}", f"{m}-01", 3000, merchant="阳光公寓物业", category="居住"
        )
        add_bills(
            uid,
            f"sal{i}",
            f"{m}-05",
            10000,
            merchant="星辉公司",
            category="工资",
            tx_type="income",
        )
        add_bills(
            uid,
            f"var{i}",
            f"{m}-15",
            900 + i * 100,
            merchant=f"超市{m}",
            category="餐饮",
        )
    return uid


# ---- 固定项识别 ----


def test_fixed_item_identification(db):
    seed_fixed_history()
    # 波动过大（2000/500/500，max÷min=4）与缺席一个月的商户都不算固定项
    add_bills(USER_A, "vw1", "2026-06-10", 2000, merchant="波动商户")
    add_bills(USER_A, "vw2", "2026-07-10", 500, merchant="波动商户")
    add_bills(USER_A, "vw3", "2026-08-10", 500, merchant="波动商户")
    add_bills(USER_A, "ab1", "2026-06-11", 100, merchant="缺席商户")
    add_bills(USER_A, "ab2", "2026-07-11", 100, merchant="缺席商户")

    data = cash_flow(USER_A, horizon=30, today=TODAY)
    items = {i["key"]: i for i in data["fixed_items"]}
    assert items["expense:阳光公寓物业"]["monthly_amount"] == 3000
    assert items["expense:阳光公寓物业"]["day_of_month"] == 1
    assert items["income:星辉公司"]["monthly_amount"] == 10000
    assert items["income:星辉公司"]["day_of_month"] == 5
    assert "expense:波动商户" not in items
    assert "expense:缺席商户" not in items
    # 每月只出现一次的超市不是固定项（有月份合计为 0）
    assert not any(k.startswith("expense:超市") for k in items)


def test_fixed_item_requires_three_full_months(db):
    # 窗口内只有两个完整月有流水：固定项识别应一无所获
    for m in ("2026-07", "2026-08"):
        add_bills(
            USER_A, f"r{m}", f"{m}-01", 3000, merchant="阳光公寓物业", category="居住"
        )
    data = cash_flow(USER_A, horizon=30, today=TODAY)
    assert data["fixed_items"] == []
    assert any("不足" in n or "未识别固定项" in n for n in data["notes"])


# ---- 分位线：样例数据与人工核算误差 < 5%（T-6.4 验收口径） ----


def test_cash_flow_p50_p90_matches_hand_calc(db):
    seed_fixed_history()
    data = cash_flow(USER_A, horizon=90, today=TODAY)

    # 人工核算：
    # 起点 = 全部流水净额 = 30000（工资）− 9000（房租）− 3000（可变）= 18000
    # 未来 90 天（09-19 ~ 12-17）：房租 3 笔 −9000，工资 3 笔 +30000
    # 可变 P50 = median(900,1000,1100) = 1000 → 1000/30×90 = 3000
    # 可变 P90 = max = 1100 → 1100/30×90 = 3300
    assert data["start_source"] == "bills_net"
    assert data["start_balance"] == pytest.approx(18000, abs=0.01)
    assert data["variable"]["p50_monthly"] == pytest.approx(1000, abs=0.01)
    assert data["variable"]["p90_monthly"] == pytest.approx(1100, abs=0.01)

    p50_end, p90_end = data["points"][-1]["p50"], data["points"][-1]["p90"]
    expected_p50 = 18000 + 30000 - 9000 - 3000  # 36000
    expected_p90 = 18000 + 30000 - 9000 - 3300  # 35700
    assert abs(p50_end - expected_p50) / expected_p50 < 0.05
    assert abs(p50_end - expected_p50) <= 1.0  # 逐日 round2 累计漂移上限
    assert abs(p90_end - expected_p90) / expected_p90 < 0.05
    assert len(data["points"]) == 90
    # 悲观线永远不高于预期线
    assert all(p["p90"] <= p["p50"] + 0.01 for p in data["points"])


# ---- 逐项排除后重算 ----


def test_exclude_fixed_item_refolds_to_variable(db):
    seed_fixed_history()
    key = "expense:阳光公寓物业"
    data = cash_flow(USER_A, horizon=30, exclude=[key], today=TODAY)

    assert [i["key"] for i in data["fixed_items"]] == ["income:星辉公司"]
    assert [i["key"] for i in data["excluded_items"]] == [key]
    # 房租流水回落进可变支出：月度合计 3900/4000/4100，P50 = 4000
    totals = {r["month"]: r["total"] for r in data["variable"]["monthly_totals"]}
    assert totals == {"2026-06": 3900, "2026-07": 4000, "2026-08": 4100}
    assert data["variable"]["p50_monthly"] == pytest.approx(4000, abs=0.01)

    # 未排除时同一数据可变支出不含房租
    plain = cash_flow(USER_A, horizon=30, today=TODAY)
    totals_plain = {r["month"]: r["total"] for r in plain["variable"]["monthly_totals"]}
    assert totals_plain == {"2026-06": 900, "2026-07": 1000, "2026-08": 1100}


def test_exclude_unknown_key_ignored(db):
    seed_fixed_history()
    data = cash_flow(USER_A, horizon=30, exclude=["expense:不存在的商户"], today=TODAY)
    assert data["excluded_items"] == []
    assert len(data["fixed_items"]) == 2


# ---- 预算建议 ----


def seed_suggestion_history(user_id):
    """2026-03 ~ 2026-08：餐饮每月 10 笔 ×100；6 月额外一笔 20000 一次性大额"""
    for m in ("2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"):
        add_bills(
            user_id,
            f"meal{m}",
            f"{m}-10",
            100,
            merchant="餐馆",
            category="餐饮",
            count=10,
        )
    add_bills(user_id, "big", "2026-06-20", 20000, merchant="数码城", category="餐饮")


def test_suggestion_excludes_one_off_large(db):
    seed_suggestion_history(USER_A)
    data = budget_suggestions(USER_A, month="2026-09", today=TODAY)
    item = next(s for s in data["suggestions"] if s["category"] == "餐饮")
    # 月度中位数 1000 → 剔除阈值 3000，20000 被剔除；建议 = 剔除后中位数 1000
    assert item["median"] == pytest.approx(1000, abs=0.01)
    assert item["suggested"] == pytest.approx(1000, abs=0.01)
    assert item["low"] == pytest.approx(900, abs=0.01)
    assert item["high"] == pytest.approx(1100, abs=0.01)
    assert item["months_used"] == 6
    assert len(item["excluded_outliers"]) == 1
    outlier = item["excluded_outliers"][0]
    assert outlier["amount"] == 20000 and outlier["merchant"] == "数码城"
    assert data["window"]["months"][0] == "2026-03"
    assert data["window"]["end"] == "2026-08-31"


def test_suggestion_needs_three_kept_months(db):
    # 出行只出现 2 个月：不给建议；偶发分类 3 个月中大额占 1 个月，剔除后仅 2 个月：也不给
    add_bills(USER_A, "tw1", "2026-04-05", 200, merchant="打车", category="交通")
    add_bills(USER_A, "tw2", "2026-05-05", 200, merchant="打车", category="交通")
    add_bills(USER_A, "od1", "2026-04-06", 500, merchant="旅行社", category="偶发")
    add_bills(USER_A, "od2", "2026-05-06", 500, merchant="旅行社", category="偶发")
    add_bills(USER_A, "od3", "2026-06-06", 20000, merchant="旅行社", category="偶发")
    data = budget_suggestions(USER_A, month="2026-09", today=TODAY)
    cats = {s["category"] for s in data["suggestions"]}
    assert "交通" not in cats
    assert "偶发" not in cats


def test_suggestion_month_defaults_and_validation(db):
    # 缺省目标月 = 当月；非法月份 400（服务层）与 400（接口层）
    data = budget_suggestions(USER_A, today=TODAY)
    assert data["month"] == "2026-09"
    with pytest.raises(ValidationError):
        budget_suggestions(USER_A, month="2026-13", today=TODAY)


def test_suggest_and_adopt_via_api(client):
    # 采纳链路：建议（只读）→ 既有 PUT /api/budget 写入 → 总览可见，随后可手动微调
    today = date.today()
    first_of_month = today.replace(day=1)

    def months_back(k):
        d = first_of_month
        for _ in range(k):
            d = (d - timedelta(days=1)).replace(day=1)
        return d.strftime("%Y-%m")

    for i in range(1, 7):
        m = months_back(i)
        add_bills(
            USER_A, f"api{m}", f"{m}-08", 50, merchant="食堂", category="餐饮", count=10
        )
    res = client.get("/api/forecast/budget-suggestions", headers=A_HEADERS)
    assert res.status_code == 200
    item = next(s for s in res.json()["data"]["suggestions"] if s["category"] == "餐饮")
    assert item["suggested"] == pytest.approx(500, abs=0.01)
    assert item["current_budget"] is None

    month = today.strftime("%Y-%m")
    assert (
        client.put(
            "/api/budget",
            headers=A_HEADERS,
            json={"month": month, "category": "餐饮", "amount": item["suggested"]},
        ).status_code
        == 200
    )
    overview = client.get(f"/api/budget?month={month}", headers=A_HEADERS).json()[
        "data"
    ]
    saved = next(i for i in overview["items"] if i["category"] == "餐饮")
    assert saved["budget"] == pytest.approx(500, abs=0.01)
    # 采纳后建议接口应回显已设置的预算
    again = client.get("/api/forecast/budget-suggestions", headers=A_HEADERS).json()[
        "data"
    ]
    item2 = next(s for s in again["suggestions"] if s["category"] == "餐饮")
    assert item2["current_budget"] == pytest.approx(500, abs=0.01)


# ---- 起点余额口径 ----


def test_start_balance_from_asset_snapshot(db):
    seed_fixed_history()
    add_bills(USER_A, "aug", "2026-08-15", 1000, merchant="八月超市", category="餐饮")
    AssetDAO.create(
        {
            "snap_date": "2026-08-01",
            "name": "储蓄卡",
            "asset_type": "asset",
            "amount": 50000,
            "remark": "",
        },
        USER_A,
    )
    data = cash_flow(USER_A, horizon=30, today=TODAY)
    # 快照净资产 50000；快照日之后的流水：08-05 工资 +10000、08-15 可变支出
    # −1100（样例）与 −1000（本用例）→ 50000 + 10000 − 2100 = 57900
    assert data["start_source"] == "asset_snapshot"
    assert data["snapshot_date"] == "2026-08-01"
    assert data["start_balance"] == pytest.approx(57900, abs=0.01)


def test_no_data_flat_curve(db):
    data = cash_flow(USER_A, horizon=30, today=TODAY)
    assert data["fixed_items"] == []
    assert data["start_balance"] == 0
    assert data["start_source"] == "bills_net"
    assert len(data["points"]) == 30
    assert all(p["p50"] == 0 and p["p90"] == 0 for p in data["points"])
    assert any("没有流水" in n or "历史不足" in n for n in data["notes"])


# ---- 接口层：参数校验与账号隔离 ----


def test_cash_flow_api_horizon_validation(client):
    assert (
        client.get(
            "/api/forecast", headers=A_HEADERS, params={"horizon": 5}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/forecast", headers=A_HEADERS, params={"horizon": 200}
        ).status_code
        == 422
    )
    res = client.get("/api/forecast", headers=A_HEADERS, params={"horizon": 30})
    assert res.status_code == 200
    assert len(res.json()["data"]["points"]) == 30
    assert (
        client.get(
            "/api/forecast/budget-suggestions",
            headers=A_HEADERS,
            params={"month": "2026-13"},
        ).status_code
        == 400
    )


def test_forecast_isolated_by_user(db):
    seed_fixed_history(USER_A)
    add_bills(
        USER_B,
        "bsal",
        "2026-06-05",
        500,
        merchant="星辉公司",
        category="工资",
        tx_type="income",
    )
    a = cash_flow(USER_A, horizon=30, today=TODAY)
    b = cash_flow(USER_B, horizon=30, today=TODAY)
    # B 只有一笔流水（单月），无固定项；看不到 A 的房租/工资/可变支出
    assert {i["key"] for i in b["fixed_items"]} == set()
    assert b["variable"]["p50_monthly"] == 0
    assert b["start_balance"] == 500  # 仅自己的一笔收入
    assert len(a["fixed_items"]) == 2


def test_budget_suggestions_respect_ledger_scope(db):
    """T-7.1 评审遗留收口：建议值与 current_budget 同账本口径

    不传账本 = 全部账本（current_budget 为各账本预算合计）；
    传账本 = 建议窗口与 current_budget 均限定该账本。
    """
    from app.db.dao.budget_dao import BudgetDAO
    from app.db.dao.ledger_dao import LedgerDAO

    other = LedgerDAO.create(name="建议口径账本", owner_id="")
    months = ["2026-04", "2026-05", "2026-06"]
    for i, month in enumerate(months):
        day = f"{month}-05 10:00:00"
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"SUGA{i}",
                tx_time=day,
                amount=10.0,
                category="餐饮",
                merchant="小账本消费",
            ),
            USER_A,
            ledger_id=1,
        )
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"SUGB{i}",
                tx_time=day,
                amount=100.0,
                category="餐饮",
                merchant="大账本消费",
            ),
            USER_A,
            ledger_id=other["id"],
        )
    BudgetDAO.upsert(USER_A, "2026-07", "餐饮", 15, 1)
    BudgetDAO.upsert(USER_A, "2026-07", "餐饮", 40, other["id"])
    today = date(2026, 7, 31)

    scoped = budget_suggestions(USER_A, month="2026-07", today=today, ledger_id=1)
    item = scoped["suggestions"][0]
    assert item["suggested"] == 10  # 仅默认账本的三个月各 10
    assert item["current_budget"] == 15  # 仅默认账本预算

    merged = budget_suggestions(USER_A, month="2026-07", today=today)
    item = merged["suggestions"][0]
    # 全部账本口径：每月合计 110（10+100），三个月中位数 110
    assert item["suggested"] == 110
    assert item["current_budget"] == 55  # 15 + 40，全部账本预算合计


def test_expense_structure_splits_fixed_and_flexible(db):
    """T-1.5：固定项（每月出现、金额稳定）进必选项，其余进可砍项；支持账本筛选"""
    from app.db.dao.ledger_dao import LedgerDAO

    today = date(2026, 9, 20)
    other = LedgerDAO.create(name="结构账本", owner_id="")
    months = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]

    def seed(month, tx_id, merchant, amount, ledger=1):
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=tx_id,
                tx_time=f"{month}-15 10:00:00",
                amount=amount,
                category="餐饮",
                merchant=merchant,
            ),
            USER_A,
            ledger_id=ledger,
        )

    # 固定项：房租每月 1000（默认账本）
    for i, m in enumerate(months):
        seed(m, f"ST-R{i}", "房东", 1000)
    # 固定项：地铁每月 60（默认账本）
    for i, m in enumerate(months):
        seed(m, f"ST-M{i}", "地铁", 60)
    # 弹性：购物只在 1 个月出现；聚餐金额波动大
    seed("2026-05", "ST-S", "商场", 900)
    for i, m in enumerate(months):
        seed(m, f"ST-C{i}", "聚餐", 40 + i * 60)

    report = forecast_service.expense_structure(USER_A, today=today)
    assert report["window"]["months"] == 6
    fixed = {f["merchant"]: f for f in report["fixed"]}
    assert set(fixed) == {"房东", "地铁"}
    assert fixed["房东"]["monthly_amount"] == 1000
    flexible = {f["merchant"]: f for f in report["flexible"]}
    assert "商场" in flexible and "聚餐" in flexible
    assert "房东" not in flexible
    # 弹性月均 = 各自窗口合计 / 6
    assert flexible["商场"]["monthly_amount"] == 150
    assert flexible["聚餐"]["monthly_amount"] == round(
        sum(40 + i * 60 for i in range(6)) / 6, 2
    )
    # 汇总：固定 1060，弹性 = (900 + 40+100+160+220+280) / 6
    assert report["fixed_monthly"] == 1060
    assert report["total_monthly"] == round(
        report["fixed_monthly"] + report["flexible_monthly"], 2
    )
    assert report["fixed_pct"] == round(1060 / report["total_monthly"] * 100, 2)

    # 账本筛选：只看结构账本 → 房租/地铁消失，全部为弹性
    for i, m in enumerate(months):
        seed(m, f"ST-O{i}", "结构消费", 10, ledger=other["id"])
    scoped = forecast_service.expense_structure(
        USER_A, today=today, ledger_id=other["id"]
    )
    # 结构账本视角：结构消费每月 10 元稳定出现 → 本账本口径下的固定项
    assert {f["merchant"] for f in scoped["fixed"]} == {"结构消费"}
    assert scoped["fixed_monthly"] == 10
    assert scoped["flexible"] == []
    assert scoped["fixed_pct"] == 100
