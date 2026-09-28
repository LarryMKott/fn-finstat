"""场景化预算模板建议测试（AI-6）：模板系数计算 / LLM 映射护栏 / 回落与报错

口径钉住清单设计：LLM 只做「选」（模板 id + 分类→槽位映射），不产出任何金额；
金额 = 收入月均 × 硬编码系数；同槽位多分类按统计中位数占比分摊；未映射分类
回落统计中位数；无收入 / 未配置 AI / 输出不合法显式报错。
"""

import json
from datetime import date

import pytest

from app.core.errors import ValidationError
from app.db.dao.bill_dao import BillDAO
from app.file_settings import AISettings
from app.services import budget_template_service, forecast_service
from app.services.ai_service import AIClientError
from app.utils.period import last_full_months
from tests.conftest import USER_A, make_bill_records

TODAY = date(2026, 9, 29)
WINDOW = last_full_months(TODAY, forecast_service.SUGGEST_WINDOW_MONTHS)
CFG = AISettings(api_key="test-key", base_url="https://api.example.com")


@pytest.fixture
def ai_ready(monkeypatch):
    """AI 已配置：patch 本服务命名空间的 load_ai_settings（conftest 的隔离夹具默认未配置）"""
    monkeypatch.setattr(budget_template_service, "load_ai_settings", lambda: CFG)


def _seed():
    """餐饮 1500/月（无大额）、购物 500/月；工资 20000/月"""
    for m in WINDOW:
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"BTP-{m}-dine",
                tx_time=f"{m}-05 10:00:00",
                tx_type="expense",
                amount=1500,
                merchant="外卖平台",
                category="餐饮",
            ),
            USER_A,
        )
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"BTP-{m}-shop",
                tx_time=f"{m}-08 10:00:00",
                tx_type="expense",
                amount=500,
                merchant="电商",
                category="购物",
            ),
            USER_A,
        )
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"BTP-{m}-inc",
                tx_time=f"{m}-01 10:00:00",
                tx_type="income",
                amount=20000,
                merchant="工资",
                category="收入",
            ),
            USER_A,
        )


def _mock_llm(payload: dict):
    """返回 mock ai_service.chat 的替换函数：固定输出 payload 对应 JSON"""

    def _fake(settings, messages, max_tokens, timeout=30):
        return json.dumps(payload, ensure_ascii=False)

    return _fake


def test_template_full_mapping(db, ai_ready, monkeypatch):
    """全映射：金额 = 收入 × 槽位系数；区间与统计值并排返回"""
    _seed()
    monkeypatch.setattr(
        budget_template_service,
        "_select_template",
        lambda settings, income, suggestions: (
            budget_template_service.TEMPLATES["student"] | {"_id": "student"},
            {"餐饮": "餐饮", "购物": "购物"},
        ),
    )
    data = budget_template_service.template_suggestion(USER_A, today=TODAY)
    assert data["income_monthly"] == 20000.0
    assert data["template"]["id"] == "student"
    items = {i["category"]: i for i in data["items"]}
    # 学生党模板：餐饮 0.30 → 6000；购物 0.10 → 2000
    assert items["餐饮"]["suggested"] == 6000.0
    assert items["餐饮"]["slot"] == "餐饮" and items["餐饮"]["source"] == "template"
    assert items["购物"]["suggested"] == 2000.0
    assert items["购物"]["low"] == 1800.0 and items["购物"]["high"] == 2200.0
    assert items["餐饮"]["statistical_suggested"] == 1500.0


def test_slot_shared_split_by_median(db, ai_ready, monkeypatch):
    """同槽位两个分类：槽位金额按统计中位数占比分摊"""
    _seed()
    # 加一个「买菜」分类与餐饮同映射到「餐饮」槽位
    for m in WINDOW:
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"BTP-{m}-veg",
                tx_time=f"{m}-06 10:00:00",
                tx_type="expense",
                amount=500,
                merchant="菜市场",
                category="买菜",
            ),
            USER_A,
        )
    monkeypatch.setattr(
        budget_template_service,
        "_select_template",
        lambda settings, income, suggestions: (
            budget_template_service.TEMPLATES["student"] | {"_id": "student"},
            {"餐饮": "餐饮", "买菜": "餐饮", "购物": "购物"},
        ),
    )
    data = budget_template_service.template_suggestion(USER_A, today=TODAY)
    items = {i["category"]: i for i in data["items"]}
    # 餐饮槽位总额 = 20000 × 0.30 = 6000；中位数占比 餐饮:买菜 = 1500:500
    assert items["餐饮"]["suggested"] == 4500.0
    assert items["买菜"]["suggested"] == 1500.0


def test_unmapped_category_falls_back_to_statistic(db, ai_ready, monkeypatch):
    """未映射/映射到未知槽位的分类回落统计中位数并标注来源"""
    _seed()
    monkeypatch.setattr(
        budget_template_service,
        "_select_template",
        lambda settings, income, suggestions: (
            budget_template_service.TEMPLATES["student"] | {"_id": "student"},
            {"餐饮": "餐饮", "购物": "不存在的槽位"},
        ),
    )
    data = budget_template_service.template_suggestion(USER_A, today=TODAY)
    items = {i["category"]: i for i in data["items"]}
    assert items["购物"]["source"] == "statistic"
    assert items["购物"]["slot"] is None
    assert items["购物"]["suggested"] == 500.0  # 与统计建议同值


def test_llm_output_guards(db, ai_ready, monkeypatch):
    """未知模板 / 非法 JSON / 缺 mapping → AIClientError；均不产出金额"""
    _seed()

    for bad in (
        {"template": "unknown-id", "mapping": {}},
        "not-json",
        {"template": "student"},
    ):
        monkeypatch.setattr(budget_template_service.ai_service, "chat", _mock_llm(bad))
        with pytest.raises(AIClientError):
            budget_template_service.template_suggestion(USER_A, today=TODAY)


def test_missing_income_rejected(db, ai_ready):
    """无收入记录：收入占比模板无意义，显式报错不做静默降级"""
    for m in WINDOW:
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"BTP-NI-{m}",
                tx_time=f"{m}-05 10:00:00",
                tx_type="expense",
                amount=1500,
                merchant="外卖平台",
                category="餐饮",
            ),
            USER_A,
        )
    with pytest.raises(ValidationError):
        budget_template_service.template_suggestion(USER_A, today=TODAY)


def test_ai_not_configured(db):
    """未配置 AI：AI_NOT_CONFIGURED 明确报错（不产生任何调用）"""
    _seed()
    with pytest.raises(AIClientError):
        budget_template_service.template_suggestion(USER_A, today=TODAY)
