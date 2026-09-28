"""MCP Server 测试（T-1.1）：JSON-RPC 握手 / 工具清单 / 工具调用 / 鉴权与开关"""

from app.db.dao.bill_dao import BillDAO
from tests.conftest import USER_A, make_bill_records

A_HEADERS = {"X-Trim-Userid": USER_A, "X-Trim-Username": "zhangsan"}


def _rpc(client, method, params=None, rpc_id=1, headers=None, **extra):
    body = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/api/mcp", json=body, headers=headers or A_HEADERS, **extra)


def test_initialize_and_tools_list(client, db):
    res = _rpc(
        client,
        "initialize",
        {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "t"},
        },
    )
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["serverInfo"]["name"] == "fn-finstat"

    tools = _rpc(client, "tools/list").json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {
        "query_bills",
        "query_summary",
        "query_budget",
        "query_savings_goals",
        # AI-7 扩展：覆盖「收支 + 预测 + 健康 + 借贷」
        "query_forecast",
        "query_health",
        "query_expense_structure",
        "query_loans",
        # AI-8 扩展：备注语义检索（零依赖 TF-IDF）
        "query_note_search",
        # AI-9 扩展：What-if 反事实模拟（线性外推）
        "query_what_if",
    } <= names
    # inputSchema 为标准 JSON Schema（MCP 客户端据此渲染参数表单）
    bills_schema = [t for t in tools if t["name"] == "query_bills"][0]["inputSchema"]
    assert bills_schema["type"] == "object"
    assert "start" in bills_schema["properties"]


def test_notifications_initialized_returns_202(client, db):
    res = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=A_HEADERS,
    )
    assert res.status_code == 202


def test_tools_call_query_bills_with_filters(client, db):
    BillDAO.insert_many(
        make_bill_records(
            2, prefix="MCP-A", tx_time="2026-09-05 10:00:00", amount=30, category="餐饮"
        ),
        USER_A,
    )
    BillDAO.insert_many(
        make_bill_records(
            1,
            prefix="MCP-B",
            tx_time="2026-09-06 10:00:00",
            amount=500,
            category="数码",
        ),
        USER_A,
    )
    res = _rpc(
        client,
        "tools/call",
        {"name": "query_bills", "arguments": {"category": "餐饮"}},
    )
    assert res.status_code == 200
    body = res.json()["result"]
    assert body["isError"] is False
    text = body["content"][0]["text"]
    assert "共 2 条" in text
    assert "餐饮" in text
    assert "数码" not in text


def test_tools_call_summary_and_budget(client, db):
    BillDAO.insert_many(
        make_bill_records(
            1,
            prefix="MCP-S",
            tx_time="2026-09-05 10:00:00",
            tx_type="income",
            amount=1000,
            category="其他",
        ),
        USER_A,
    )
    BillDAO.insert_many(
        make_bill_records(
            1,
            prefix="MCP-E",
            tx_time="2026-09-06 10:00:00",
            tx_type="expense",
            amount=200,
            category="餐饮",
        ),
        USER_A,
    )
    summary = _rpc(
        client,
        "tools/call",
        {
            "name": "query_summary",
            "arguments": {"start": "2026-09-01", "end": "2026-09-30"},
        },
    ).json()["result"]
    assert "收入 1000.0 元" in summary["content"][0]["text"]
    assert "支出 200.0 元" in summary["content"][0]["text"]

    client.put(
        "/api/budget",
        json={"month": "2026-09", "category": "餐饮", "amount": 300},
        headers=A_HEADERS,
    )
    budget = _rpc(
        client,
        "tools/call",
        {"name": "query_budget", "arguments": {"month": "2026-09"}},
    ).json()["result"]["content"][0]["text"]
    assert "餐饮：预算 300.0 元，已用 200.0 元" in budget


def test_tools_call_ai7_extensions(client, db):
    """AI-7 扩展工具：预测/健康/支出结构/借贷（数据相对真实今天构造，防时效）"""
    from datetime import date

    from app.db.dao.loan_dao import LoanDAO
    from app.utils.period import last_full_months

    months = last_full_months(date.today(), 6)
    for i, m in enumerate(months):
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"MCP-FX-{i}",
                tx_time=f"{m}-05 10:00:00",
                tx_type="expense",
                amount=30,
                merchant="月月扣",
                category="会员订阅",
            ),
            USER_A,
        )
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"MCP-INC-{i}",
                tx_time=f"{m}-01 10:00:00",
                tx_type="income",
                amount=5000,
                merchant="工资",
            ),
            USER_A,
        )
    loan = LoanDAO.create(
        USER_A,
        {
            "direction": "lend",
            "counterparty": "张三",
            "principal": 1000.0,
            "loan_date": f"{months[-1]}-10",
            "note": "",
            "status": "open",
        },
    )
    LoanDAO.add_payment(
        loan["id"], {"amount": 200.0, "pay_date": f"{months[-1]}-20", "note": ""}
    )

    forecast_text = _rpc(
        client, "tools/call", {"name": "query_forecast", "arguments": {"horizon": 30}}
    ).json()["result"]["content"][0]["text"]
    assert "起点余额" in forecast_text and "P50" in forecast_text
    assert "固定支出：月月扣 每月 30.0 元" in forecast_text

    health_text = _rpc(client, "tools/call", {"name": "query_health"}).json()["result"][
        "content"
    ][0]["text"]
    assert "储蓄率：99.4%" in health_text
    assert "负债率：暂无法评估" in health_text

    structure_text = _rpc(
        client, "tools/call", {"name": "query_expense_structure"}
    ).json()["result"]["content"][0]["text"]
    assert "必选项 30.0 元/月" in structure_text
    assert "必选：月月扣 每月 30.0 元" in structure_text

    loans_text = _rpc(client, "tools/call", {"name": "query_loans"}).json()["result"][
        "content"
    ][0]["text"]
    assert "应收（借出未结）合计 800.0 元" in loans_text
    assert "借出（应收） 张三" in loans_text and "剩余 800.0 元" in loans_text


def test_tools_call_note_search(client, db):
    """AI-8 备注语义检索工具：模糊描述命中备注，空检索词按 isError 返回"""
    BillDAO.insert_many(
        make_bill_records(
            1,
            prefix="MCP-NS",
            merchant="天猫超市",
            remark="给老妈买的按摩仪",
        ),
        USER_A,
    )
    text = _rpc(
        client,
        "tools/call",
        {"name": "query_note_search", "arguments": {"q": "给家里人买东西"}},
    ).json()["result"]["content"][0]["text"]
    assert "给老妈买的按摩仪" in text
    assert "相关度" in text

    missing = _rpc(
        client, "tools/call", {"name": "query_note_search", "arguments": {}}
    ).json()["result"]
    assert missing["isError"] is True
    assert "检索词" in missing["content"][0]["text"]


def test_tools_call_what_if(client, db):
    """AI-9 What-if 工具：调整清单文本解析出情景，格式不合法按 isError 返回"""
    from datetime import date

    from app.utils.period import last_full_months

    today = date.today()
    for m in last_full_months(today, 6):
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"MCP-WIF-{m}",
                tx_time=f"{m}-05 10:00:00",
                tx_type="expense",
                amount=1500,
                merchant="外卖平台",
                category="餐饮",
            ),
            USER_A,
        )
    text = _rpc(
        client,
        "tools/call",
        {
            "name": "query_what_if",
            "arguments": {"adjustments": "餐饮:800", "months": 12},
        },
    ).json()["result"]["content"][0]["text"]
    assert "餐饮：1500.0 → 800.0 元/月" in text
    assert "累计 +8400.0 元" in text

    bad = _rpc(
        client,
        "tools/call",
        {"name": "query_what_if", "arguments": {"adjustments": "餐饮 800"}},
    ).json()["result"]
    assert bad["isError"] is True
    assert "冒号" in bad["content"][0]["text"]


def test_tools_call_unknown_tool_is_error_result(client, db):
    res = _rpc(client, "tools/call", {"name": "write_bill", "arguments": {}})
    body = res.json()["result"]
    assert body["isError"] is True
    assert "未知工具" in body["content"][0]["text"]


def test_unknown_method_is_rpc_error(client, db):
    res = _rpc(client, "resources/list")
    assert res.json()["error"]["code"] == -32601


def test_mcp_disabled_returns_404(client, db, monkeypatch):
    import app.api.mcp as mcp_api

    monkeypatch.setattr(mcp_api, "MCP_ENABLED", False)
    res = _rpc(client, "tools/list")
    assert res.status_code == 404


def test_parse_error_returns_400(client, db):
    res = client.post(
        "/api/mcp",
        content=b"not json",
        headers={**A_HEADERS, "content-type": "application/json"},
    )
    assert res.status_code == 400
