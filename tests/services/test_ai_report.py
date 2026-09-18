"""AI 消费分析报告测试：周期统计、prompt 组装、报告解析、归档与外部请求 mock"""

import json

import pytest

from app.config import AISettings
from app.db.dao.bill_dao import BillDAO
from app.services import ai_service
from app.utils.period import (
    period_label,
    period_range,
    prev_period,
    valid_period,
)
from tests.conftest import USER_A

CFG = AISettings(
    api_key="sk-test", base_url="https://api.example.com", model="deepseek-chat"
)


def seed_month():
    BillDAO.insert_many(
        [
            {
                "tx_time": "2026-09-01 08:00:00",
                "account": "wechat",
                "tx_type": "expense",
                "merchant": "肯德基",
                "amount": 30.0,
                "category": "餐饮",
                "tx_id": "RP-0001",
                "remark": "",
            },
            {
                "tx_time": "2026-08-01 08:00:00",
                "account": "wechat",
                "tx_type": "expense",
                "merchant": "肯德基",
                "amount": 10.0,
                "category": "餐饮",
                "tx_id": "RP-0002",
                "remark": "",
            },
        ],
        USER_A,
    )


def seed_quarter():
    """2026 Q3（7-9 月）数据：3 个月各一笔，便于环比 Q2（4-6 月为空）"""
    BillDAO.insert_many(
        [
            {
                "tx_time": "2026-07-15 12:00:00",
                "account": "wechat",
                "tx_type": "expense",
                "merchant": "京东",
                "amount": 100.0,
                "category": "数码",
                "tx_id": "Q3-0001",
                "remark": "",
            },
            {
                "tx_time": "2026-08-15 12:00:00",
                "account": "wechat",
                "tx_type": "expense",
                "merchant": "京东",
                "amount": 50.0,
                "category": "数码",
                "tx_id": "Q3-0002",
                "remark": "",
            },
            {
                "tx_time": "2026-09-15 12:00:00",
                "account": "wechat",
                "tx_type": "expense",
                "merchant": "京东",
                "amount": 20.0,
                "category": "数码",
                "tx_id": "Q3-0003",
                "remark": "",
            },
        ],
        USER_A,
    )


# ---- 周期工具：扩展后的 period.py 校验、边界与上一周期 ----


def test_period_validation_and_range():
    """valid_period / period_range / prev_period / period_label 跨四种周期"""
    assert valid_period("month", "2026-09")
    assert valid_period("quarter", "2026-Q1")
    assert valid_period("half", "2026-H2")
    assert valid_period("year", "2026")
    # 非法值
    assert not valid_period("month", "2026-13")
    assert not valid_period("quarter", "2026-Q5")
    assert not valid_period("half", "2026-H3")
    assert not valid_period("year", "2026X")
    assert not valid_period("unknown", "2026")

    # 边界：含当日两端
    assert period_range("month", "2026-09") == ("2026-09-01", "2026-09-30")
    assert period_range("quarter", "2026-Q1") == ("2026-01-01", "2026-03-31")
    assert period_range("quarter", "2026-Q4") == ("2026-10-01", "2026-12-31")
    assert period_range("half", "2026-H1") == ("2026-01-01", "2026-06-30")
    assert period_range("half", "2026-H2") == ("2026-07-01", "2026-12-31")
    assert period_range("year", "2026") == ("2026-01-01", "2026-12-31")

    # 上一周期
    assert prev_period("month", "2026-09") == "2026-08"
    assert prev_period("month", "2026-01") == "2025-12"
    assert prev_period("quarter", "2026-Q1") == "2025-Q4"
    assert prev_period("quarter", "2026-Q3") == "2026-Q2"
    assert prev_period("half", "2026-H1") == "2025-H2"
    assert prev_period("half", "2026-H2") == "2026-H1"
    assert prev_period("year", "2026") == "2025"

    # 人类可读标签
    assert period_label("month", "2026-09") == "2026 年 9 月"
    assert period_label("quarter", "2026-Q3") == "2026 年 Q3 季度"
    assert period_label("half", "2026-H1") == "2026 年上半年"
    assert period_label("half", "2026-H2") == "2026 年下半年"
    assert period_label("year", "2026") == "2026 年度"


# ---- 月度报告（兼容旧接口，保持原测试覆盖）----


def test_report_context_collects_stats(db):
    seed_month()
    ctx = ai_service.report_context(USER_A, "2026-09")
    assert ctx["month"] == "2026-09" and ctx["prev_month"] == "2026-08"
    assert ctx["this_expense"] == 30 and ctx["prev_expense"] == 10
    assert ctx["categories"] == [{"name": "餐饮", "expense": 30.0}]
    assert ctx["top_merchants"][0]["merchant"] == "肯德基"
    assert ctx["max_expense_day"] == "2026-09-01"
    # 上月分类快照供环比
    assert ctx["prev_categories"] == {"餐饮": 10.0}


def test_report_prompt_contains_numbers(db):
    seed_month()
    prompt = ai_service._build_report_prompt(
        ai_service.report_context(USER_A, "2026-09")
    )
    assert "2026-09" in prompt and "30.0" in prompt
    assert "本月分类支出：餐饮 30.0元" in prompt


def test_generate_month_report_with_mocked_chat(db, monkeypatch):
    seed_month()
    monkeypatch.setattr(ai_service, "load_ai_settings", lambda: CFG)
    captured = {}

    def fake_chat(settings, messages, max_tokens):
        captured["messages"] = messages
        return json.dumps({"report": "# 九月报告\n\n支出 30 元。"})

    monkeypatch.setattr(ai_service, "_chat", fake_chat)
    result = ai_service.generate_month_report(USER_A, "2026-09")
    assert result["month"] == "2026-09"
    assert result["report"].startswith("# 九月报告")
    assert "财务分析师" in captured["messages"][0]["content"]


def test_generate_month_report_requires_api_key(db):
    seed_month()
    with pytest.raises(ai_service.AIClientError):
        ai_service.generate_month_report(USER_A, "2026-09")


def test_generate_month_report_default_month_and_invalid(db, monkeypatch):
    monkeypatch.setattr(ai_service, "load_ai_settings", lambda: CFG)

    monkeypatch.setattr(ai_service, "_chat", lambda s, m, max_tokens: '{"report": "r"}')
    # 缺省月份 → 上个月（2026-09 是固定参照：直接验证格式校验与解析路径）
    result = ai_service.generate_month_report(USER_A)
    assert result["month"].startswith("2026-0") or result["month"].startswith("2025-")

    with pytest.raises(ai_service.AIClientError):
        ai_service.generate_month_report(USER_A, "2026-13")


def test_parse_report_handles_code_fence():
    fenced = "```json\n" + json.dumps({"report": "报告内容"}) + "\n```"
    assert ai_service._parse_report(fenced) == "报告内容"
    with pytest.raises(ai_service.AIClientError):
        ai_service._parse_report("不是 JSON")
    with pytest.raises(ai_service.AIClientError):
        ai_service._parse_report(json.dumps({"nope": 1}))


def test_report_api_endpoint(client, db, monkeypatch):
    seed_month()
    # 未配置密钥 → 400 且带提示
    res = client.post(
        "/api/ai/report", headers={"X-Trim-Userid": USER_A}, json={"month": "2026-09"}
    )
    assert res.status_code == 400
    assert "API Key" in res.json()["msg"]

    monkeypatch.setattr(ai_service, "load_ai_settings", lambda: CFG)
    monkeypatch.setattr(
        ai_service, "_chat", lambda s, m, max_tokens: '{"report": "九月支出 30 元"}'
    )
    res = client.post(
        "/api/ai/report", headers={"X-Trim-Userid": USER_A}, json={"month": "2026-09"}
    )
    assert res.status_code == 200
    assert res.json()["data"] == {"month": "2026-09", "report": "九月支出 30 元"}


# ---- 周期报告扩展（季/半年/年）----


def test_report_context_period_quarter(db):
    """季度统计：Q3 三笔合计 170，Q2 为空（环比上期为 0）"""
    seed_quarter()
    ctx = ai_service.report_context_period(USER_A, "quarter", "2026-Q3")
    assert ctx["period_type"] == "quarter"
    assert ctx["period_value"] == "2026-Q3"
    assert ctx["period_label"] == "2026 年 Q3 季度"
    assert ctx["prev_period_value"] == "2026-Q2"
    assert ctx["this_expense"] == 170.0
    assert ctx["prev_expense"] == 0
    assert ctx["categories"] == [{"name": "数码", "expense": 170.0}]
    assert ctx["top_merchants"][0]["merchant"] == "京东"


def test_build_report_prompt_quarter_uses_period_label(db):
    """季/半年/年走通用文案，不应出现「本月/上月」字样"""
    seed_quarter()
    ctx = ai_service.report_context_period(USER_A, "quarter", "2026-Q3")
    prompt = ai_service._build_report_prompt(ctx)
    assert "2026 年 Q3 季度" in prompt
    assert "本期" in prompt and "上期" in prompt
    assert "本月" not in prompt and "上月" not in prompt
    assert "170.0" in prompt


def test_generate_report_quarter_with_mocked_chat(db, monkeypatch):
    seed_quarter()
    monkeypatch.setattr(ai_service, "load_ai_settings", lambda: CFG)
    monkeypatch.setattr(
        ai_service,
        "_chat",
        lambda s, m, max_tokens: json.dumps({"report": "# Q3 报告\n\n支出 170 元。"}),
    )
    result = ai_service.generate_report(USER_A, "quarter", "2026-Q3")
    assert result["period_type"] == "quarter"
    assert result["period_value"] == "2026-Q3"
    assert result["title"] == "2026 年 Q3 季度消费分析报告"
    assert result["report"].startswith("# Q3 报告")
    assert result["context"]["this_expense"] == 170.0


def test_generate_report_requires_api_key(db):
    """未配置密钥 → AIClientError（与月度一致）"""
    with pytest.raises(ai_service.AIClientError):
        ai_service.generate_report(USER_A, "quarter", "2026-Q3")


def test_generate_report_rejects_invalid_period(db, monkeypatch):
    monkeypatch.setattr(ai_service, "load_ai_settings", lambda: CFG)
    # 非法 period_type
    with pytest.raises(ai_service.AIClientError):
        ai_service.generate_report(USER_A, "century", "2026")
    # 非法 period_value
    with pytest.raises(ai_service.AIClientError):
        ai_service.generate_report(USER_A, "quarter", "2026-Q5")
    with pytest.raises(ai_service.AIClientError):
        ai_service.generate_report(USER_A, "year", "2026X")


def test_generate_report_endpoint(client, db, monkeypatch):
    """POST /api/ai/report/generate 端到端：未配置密钥与成功两路径"""
    seed_quarter()
    # 未配置密钥 → 400
    res = client.post(
        "/api/ai/report/generate",
        headers={"X-Trim-Userid": USER_A},
        json={"period_type": "quarter", "period_value": "2026-Q3"},
    )
    assert res.status_code == 400
    assert "API Key" in res.json()["msg"]

    monkeypatch.setattr(ai_service, "load_ai_settings", lambda: CFG)
    monkeypatch.setattr(
        ai_service,
        "_chat",
        lambda s, m, max_tokens: json.dumps({"report": "# Q3 报告\n\n支出 170 元。"}),
    )
    res = client.post(
        "/api/ai/report/generate",
        headers={"X-Trim-Userid": USER_A},
        json={"period_type": "quarter", "period_value": "2026-Q3"},
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["period_type"] == "quarter"
    assert data["period_value"] == "2026-Q3"
    assert data["title"] == "2026 年 Q3 季度消费分析报告"
    assert "# Q3 报告" in data["report"]


# ---- 归档：upsert / 列表 / 详情 / 删除 ----


def test_archive_and_list_and_get_and_delete(db):
    """完整归档生命周期：upsert 覆盖、列表、详情、删除、跨账号隔离"""
    ctx_payload = {"this_expense": 170.0, "period_label": "2026 年 Q3 季度"}

    # 首次归档
    saved = ai_service.archive_report(
        user_id=USER_A,
        period_type="quarter",
        period_value="2026-Q3",
        title="2026 年 Q3 季度消费分析报告",
        content="# Q3 报告\n\n支出 170 元。",
        stats_summary=ctx_payload,
    )
    assert saved["id"] > 0
    assert saved["period_type"] == "quarter"
    assert saved["period_value"] == "2026-Q3"
    assert saved["created_at"] == saved["updated_at"]

    # 再次归档同周期 → 覆盖（upsert）
    import time as _time

    _time.sleep(0.01)
    updated = ai_service.archive_report(
        user_id=USER_A,
        period_type="quarter",
        period_value="2026-Q3",
        title="2026 年 Q3 季度消费分析报告（更新）",
        content="# Q3 报告 v2",
        stats_summary=ctx_payload,
    )
    assert updated["id"] == saved["id"]
    assert updated["title"].endswith("（更新）")
    assert updated["updated_at"] >= saved["updated_at"]
    assert updated["created_at"] == saved["created_at"]

    # 列表：不返回正文
    items = ai_service.list_archived(USER_A)
    assert len(items) == 1
    assert items[0]["id"] == saved["id"]
    assert "content" not in items[0]
    assert "stats_summary" not in items[0]

    # 按 period_type 过滤
    assert ai_service.list_archived(USER_A, "month") == []
    assert len(ai_service.list_archived(USER_A, "quarter")) == 1

    # 详情：含正文与统计上下文
    detail = ai_service.get_archived(USER_A, saved["id"])
    assert detail["content"] == "# Q3 报告 v2"
    assert json.loads(detail["stats_summary"])["this_expense"] == 170.0

    # 跨账号隔离：USER_B 查不到 USER_A 的归档（DAO 层返回 None，service 层抛 NotFoundError）
    from tests.conftest import USER_B

    from app.db.dao.ai_report_dao import AIReportDAO

    assert AIReportDAO.get(USER_B, saved["id"]) is None
    assert ai_service.list_archived(USER_B) == []

    # 删除
    assert ai_service.delete_archived(USER_A, saved["id"]) is True
    assert ai_service.delete_archived(USER_A, saved["id"]) is False
    assert ai_service.list_archived(USER_A) == []


def test_archive_rejects_invalid_period(db):
    """非法周期类型/标识直接拒绝归档"""
    with pytest.raises(ai_service.AIClientError):
        ai_service.archive_report(
            user_id=USER_A,
            period_type="century",
            period_value="2026",
            title="t",
            content="c",
        )
    with pytest.raises(ai_service.AIClientError):
        ai_service.archive_report(
            user_id=USER_A,
            period_type="year",
            period_value="2026X",
            title="t",
            content="c",
        )


def test_get_archived_nonexistent_raises(db):
    """查询不存在的归档报告 → NotFoundError"""
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        ai_service.get_archived(USER_A, 9999)


def test_archive_endpoint_full_lifecycle(client, db):
    """归档 API 端到端：archive / list / get / delete"""
    payload = {
        "period_type": "half",
        "period_value": "2026-H1",
        "title": "2026 上半年报告",
        "content": "# 上半年报告\n\n支出 1000 元。",
        "stats_summary": {"this_expense": 1000.0},
    }
    res = client.post(
        "/api/ai/report/archive",
        headers={"X-Trim-Userid": USER_A},
        json=payload,
    )
    assert res.status_code == 200
    saved = res.json()["data"]
    assert saved["period_type"] == "half"
    assert saved["period_value"] == "2026-H1"

    # 列表
    res = client.get("/api/ai/report/list", headers={"X-Trim-Userid": USER_A})
    assert res.status_code == 200
    items = res.json()["data"]
    assert len(items) == 1
    assert items[0]["id"] == saved["id"]

    # 详情
    res = client.get(
        f"/api/ai/report/{saved['id']}",
        headers={"X-Trim-Userid": USER_A},
    )
    assert res.status_code == 200
    detail = res.json()["data"]
    assert detail["content"] == "# 上半年报告\n\n支出 1000 元。"

    # 详情不存在 → 404
    res = client.get(
        "/api/ai/report/9999",
        headers={"X-Trim-Userid": USER_A},
    )
    assert res.status_code == 404

    # 删除
    res = client.delete(
        f"/api/ai/report/{saved['id']}",
        headers={"X-Trim-Userid": USER_A},
    )
    assert res.status_code == 200
    assert res.json()["data"] == {"ok": True}

    # 删除后再删 → ok=False（不存在）
    res = client.delete(
        f"/api/ai/report/{saved['id']}",
        headers={"X-Trim-Userid": USER_A},
    )
    assert res.status_code == 200
    assert res.json()["data"] == {"ok": False}


def test_archive_list_cross_user_isolation(client, db):
    """归档列表跨账号隔离：USER_B 看不到 USER_A 的归档"""
    payload = {
        "period_type": "year",
        "period_value": "2026",
        "title": "2026 年度报告",
        "content": "# 年度报告",
        "stats_summary": None,
    }
    client.post(
        "/api/ai/report/archive",
        headers={"X-Trim-Userid": USER_A},
        json=payload,
    )
    res = client.get("/api/ai/report/list", headers={"X-Trim-Userid": "10002"})
    assert res.status_code == 200
    assert res.json()["data"] == []
