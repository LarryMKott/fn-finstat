"""场景化预算模板端点测试（AI-6）：POST /api/forecast/budget-template

服务层逻辑已在 test_budget_template.py 覆盖（mock LLM），本文件只钉：
未配置 AI 的统一错误体、mock 通后的响应结构、账本/月份参数透传。
"""

import json

from app.db.dao.bill_dao import BillDAO
from app.file_settings import AISettings
from app.services import budget_template_service
from app.utils.period import last_full_months
from datetime import date
from tests.conftest import USER_A, make_bill_records

from tests.api.test_api import A_HEADERS, B_HEADERS

TODAY = date(2026, 9, 29)
CFG = AISettings(api_key="test-key", base_url="https://api.example.com")
WINDOW = last_full_months(TODAY, 6)


def _seed(user: str, prefix: str) -> None:
    for m in WINDOW:
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"{prefix}-{m}",
                tx_time=f"{m}-05 10:00:00",
                tx_type="expense",
                amount=1500,
                merchant="外卖平台",
                category="餐饮",
            ),
            user,
        )
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"{prefix}-inc-{m}",
                tx_time=f"{m}-01 10:00:00",
                tx_type="income",
                amount=20000,
                merchant="工资",
                category="收入",
            ),
            user,
        )


def test_budget_template_requires_ai_config(client, db):
    """未配置 AI：400 + AI_NOT_CONFIGURED 错误码，不产生调用"""
    _seed(USER_A, "BTP-A")
    res = client.post("/api/forecast/budget-template", json={}, headers=A_HEADERS)
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == 48001


def test_budget_template_returns_template_structure(client, db, monkeypatch):
    """mock 通后返回模板 + 逐条 source 标注；映射之外的槽位字段齐全"""
    _seed(USER_A, "BTP-A")
    monkeypatch.setattr(budget_template_service, "load_ai_settings", lambda: CFG)
    llm_payload = json.dumps(
        {"template": "student", "mapping": {"餐饮": "餐饮"}}, ensure_ascii=False
    )
    monkeypatch.setattr(
        budget_template_service.ai_service,
        "chat",
        lambda settings, messages, max_tokens, timeout=30: llm_payload,
    )
    res = client.post("/api/forecast/budget-template", json={}, headers=A_HEADERS)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["income_monthly"] == 20000.0
    assert data["template"]["id"] == "student"
    items = {i["category"]: i for i in data["items"]}
    assert items["餐饮"]["suggested"] == 6000.0
    assert items["餐饮"]["source"] == "template"
    assert "notes" in data and len(data["window"]["months"]) == 6


def test_budget_template_user_isolation(client, db, monkeypatch):
    """B 账号无数据：统计建议为空 → 400（无法生成），而非串到 A 的数据"""
    _seed(USER_A, "BTP-A")
    monkeypatch.setattr(budget_template_service, "load_ai_settings", lambda: CFG)
    res = client.post("/api/forecast/budget-template", json={}, headers=B_HEADERS)
    assert res.status_code == 400
