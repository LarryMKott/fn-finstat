"""家庭月度 AI 复盘测试（AI-10）：聚合门控前移 / 上下文裁剪 / 预算联动

强约束钉住清单原文：复盘必须只基于聚合值；allow_detail_view 门控必须
**前移**——关闭时成员级聚合完全不进 LLM 上下文（而非靠提示词约束）。
"""

import json

import pytest

from app.core.errors import NotFoundError, ValidationError
from app.db.dao.bill_dao import BillDAO
from app.file_settings import AISettings
from app.schemas.budget import BudgetUpsert
from app.services import budget_service, family_ai_service, family_service
from app.services.ai_service import AIClientError
from tests.conftest import USER_A, USER_B, make_bill_records

CFG = AISettings(api_key="test-key", base_url="https://api.example.com")
MONTH = "2026-08"


def _setup_family():
    """A 建家庭（管理员）并邀请 B 加入，两人各记 3 个月度账单"""
    created = family_service.create_family("测试家庭", USER_A, nickname="管理员")
    code = created["invite_code"]
    family_service.join_family(code, USER_B, nickname="成员乙")
    for uid, prefix in ((USER_A, "FAR-A"), (USER_B, "FAR-B")):
        BillDAO.insert_many(
            make_bill_records(
                2,
                prefix=prefix,
                tx_time=f"{MONTH}-05 10:00:00",
                tx_type="expense",
                amount=1000,
                merchant="超市",
                category="餐饮",
            ),
            uid,
        )
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"{prefix}-inc",
                tx_time=f"{MONTH}-01 10:00:00",
                tx_type="income",
                amount=10000,
                merchant="工资",
                category="收入",
            ),
            uid,
        )


def _capture_chat(monkeypatch, reply="复盘正文：本月结余为正。"):
    """替换 chat 并捕获发给模型的 messages，返回 (fake, captured)"""
    captured: list[dict] = []

    def fake_chat(settings, messages, max_tokens, timeout=60):
        captured.append(messages)
        return reply

    monkeypatch.setattr(family_ai_service.ai_service, "chat", fake_chat)
    monkeypatch.setattr(family_ai_service, "load_ai_settings", lambda: CFG)
    return captured


def test_context_excludes_members_when_gated(db, monkeypatch):
    """门控关闭（默认）：成员级聚合完全不进上下文——门控前移的核心断言"""
    _setup_family()
    captured = _capture_chat(monkeypatch)
    data = family_ai_service.monthly_review(USER_A, MONTH)
    assert data["member_detail_included"] is False
    context = json.loads(captured[0][1]["content"])
    assert "members" not in context
    assert context["totals"]["expense"] == 4000.0
    assert context["totals"]["income"] == 20000.0
    assert context["categories"][0]["name"] == "餐饮"
    assert "budget" not in context  # 未设家庭预算则不进上下文


def test_context_includes_member_aggregates_when_allowed(db, monkeypatch):
    """门控开启：成员收支**合计**进上下文，但仍不得携带任何明细字段"""
    _setup_family()
    family_service.update_settings(USER_A, type("P", (), {"allow_detail_view": True})())
    captured = _capture_chat(monkeypatch)
    data = family_ai_service.monthly_review(USER_A, MONTH)
    assert data["member_detail_included"] is True
    context = json.loads(captured[0][1]["content"])
    assert len(context["members"]) == 2
    for m in context["members"]:
        assert set(m) == {"nickname", "income", "expense"}  # 只有聚合字段
        assert m["expense"] == 2000.0
    # 家庭预算也一并进入上下文
    budget_service.upsert_family_budget(
        BudgetUpsert(month=MONTH, category="餐饮", amount=3000), USER_A
    )
    captured2 = _capture_chat(monkeypatch)
    family_ai_service.monthly_review(USER_A, MONTH)
    context2 = json.loads(captured2[0][1]["content"])
    assert context2["budget"]["items"] == [
        {"category": "餐饮", "budget": 3000.0, "expense": 4000.0}
    ]


def test_empty_month_rejected(db, monkeypatch):
    """该月无任何记账：不烧 API 费用，显式报错"""
    _setup_family()
    _capture_chat(monkeypatch)
    with pytest.raises(ValidationError):
        family_ai_service.monthly_review(USER_A, "2026-01")


def test_non_member_rejected(db, monkeypatch):
    """非家庭成员：沿用家庭服务的成员校验，直接拒绝"""
    _setup_family()
    _capture_chat(monkeypatch)
    with pytest.raises(NotFoundError):
        family_ai_service.monthly_review("user-c", MONTH)


def test_ai_not_configured(db):
    """未配置 AI：明确报错（不发起调用）"""
    _setup_family()
    with pytest.raises(AIClientError):
        family_ai_service.monthly_review(USER_A, MONTH)


def test_invalid_month_format(db, monkeypatch):
    """月份格式不合法：参数校验先行"""
    _setup_family()
    _capture_chat(monkeypatch)
    with pytest.raises(ValidationError):
        family_ai_service.monthly_review(USER_A, "2026/08")
