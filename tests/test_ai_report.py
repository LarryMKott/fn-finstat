"""AI 月度消费报告测试：统计数据收集、prompt 组装与报告解析（外部请求全部 mock）"""

import json

import pytest

from app.config import AISettings
from app.db.dao.bill_dao import BillDAO
from app.services import ai_service
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

    def fake_report(user_id, month):
        return {"month": month, "report": "ok"}

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
