"""AI 财务教练测试（T-1.6）：结构化摘要构建 / DeepSeek 调用 / 校验 / 隐私红线

隐私红线：DeepSeek 请求体只包含结构化聚合摘要，绝不包含任何单笔流水。
"""

from datetime import date

import pytest

from app.core.errors import ValidationError
from app.services.ai_service import AIClientError
from app.db.dao.bill_dao import BillDAO
from app.services import coach_service
from tests.conftest import USER_A, make_bill_records

TODAY = date(2026, 9, 20)


def _seed_months():
    """近 6 个完整月：每月收入 100，支出 40 餐饮 + 30 交通"""
    months = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
    for i, m in enumerate(months):
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"CH-I{i}",
                tx_time=f"{m}-10 10:00:00",
                tx_type="income",
                amount=100,
                category="其他",
            ),
            USER_A,
        )
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"CH-E{i}",
                tx_time=f"{m}-20 10:00:00",
                tx_type="expense",
                amount=40,
                category="餐饮",
            ),
            USER_A,
        )
        BillDAO.insert_many(
            make_bill_records(
                1,
                prefix=f"CH-T{i}",
                tx_time=f"{m}-25 10:00:00",
                tx_type="expense",
                amount=30,
                category="交通",
            ),
            USER_A,
        )


def _capture_chat(monkeypatch, captured):
    """拦截 ai_service.chat，捕获 messages 并返回固定回答"""

    def fake_chat(settings, messages, max_tokens, timeout=60):
        captured["messages"] = messages
        return "建议控制餐饮支出。"

    monkeypatch.setattr(coach_service.ai_service, "chat", fake_chat)


def _fake_settings():
    from app.file_settings import AISettings

    return AISettings(api_key="sk-test", enabled=True)


def test_context_contains_aggregates_not_raw_bills(db, monkeypatch):
    """上下文包含结构化聚合（月均 / 分类 / 预算），不包含任何单笔流水字段"""
    _seed_months()
    captured = {}
    _capture_chat(monkeypatch, captured)

    result = coach_service.coach_chat(
        USER_A, "我的消费习惯怎么样？", today=TODAY, settings=_fake_settings()
    )
    context = result["context"]
    assert "月均收入" in context
    assert "月均支出" in context
    assert "储蓄率" in context
    assert "分类支出" in context

    # 隐私红线：上下文不得包含单笔流水的 tx_time / tx_id / tx_type 等原始字段
    assert "tx_time" not in context
    assert "tx_id" not in context
    assert "2026-03-10" not in context  # 单笔流水日期


def test_chat_sends_question_and_returns_answer(db, monkeypatch):
    _seed_months()
    captured = {}
    _capture_chat(monkeypatch, captured)

    result = coach_service.coach_chat(
        USER_A, "怎么才能多存钱？", today=TODAY, settings=_fake_settings()
    )
    assert result["answer"] == "建议控制餐饮支出。"
    assert result["question"] == "怎么才能多存钱？"
    # system 提示词 + user 消息
    assert len(captured["messages"]) >= 2
    assert captured["messages"][0]["role"] == "system"
    assert "财务教练" in captured["messages"][0]["content"]


def test_chat_followup_history_passed(db, monkeypatch):
    """追问上下文：历史问答文本传给模型（≤3 轮），但摘要不重复"""
    captured = {}
    _capture_chat(monkeypatch, captured)
    history = [
        {"question": "上月支出多少", "answer": "上月支出 70 元"},
        {"question": "怎么控制", "answer": "设置预算"},
    ]
    coach_service.coach_chat(
        USER_A, "那餐饮呢？", history=history, today=TODAY, settings=_fake_settings()
    )
    # system + 2 轮历史（4 条） + 当前问题 = 6 条
    assert len(captured["messages"]) == 6
    assert captured["messages"][1]["content"] == "上月支出多少"
    assert captured["messages"][3]["content"] == "怎么控制"


def test_empty_question_raises(db):
    with pytest.raises(ValidationError):
        coach_service.coach_chat(USER_A, "  ", today=TODAY, settings=_fake_settings())


def test_overlong_question_raises(db):
    with pytest.raises(ValidationError):
        coach_service.coach_chat(
            USER_A, "x" * 501, today=TODAY, settings=_fake_settings()
        )


def test_not_ready_raises(db):
    from app.file_settings import AISettings

    with pytest.raises(AIClientError, match="API Key"):
        coach_service.coach_chat(
            USER_A,
            "怎么存钱",
            today=TODAY,
            settings=AISettings(api_key="", enabled=False),
        )


def test_context_budget_section(db, monkeypatch):
    """有预算时上下文包含预算进度"""
    from app.db.dao.budget_dao import BudgetDAO

    BudgetDAO.upsert(USER_A, "2026-09", "餐饮", 300, 1)
    captured = {}
    _capture_chat(monkeypatch, captured)
    coach_service.coach_chat(
        USER_A, "预算用得怎么样", today=date(2026, 9, 20), settings=_fake_settings()
    )
    assert "预算" in captured["messages"][-1]["content"]


def test_context_intent_gating(db):
    """AI-3：扩展板块按问题意图裁剪——问负债才带借贷，不问不带"""
    from app.db.dao.loan_dao import LoanDAO

    LoanDAO.create(
        USER_A,
        {
            "direction": "lend",
            "counterparty": "老王",
            "principal": 500,
            "loan_date": "2026-08-01",
        },
    )
    _seed_months()

    asked = coach_service._build_context(USER_A, TODAY, question="我还有多少负债要还")
    assert "借贷台账" in asked and "应收" in asked

    not_asked = coach_service._build_context(USER_A, TODAY, question="这个月花费如何")
    assert "借贷台账" not in not_asked
    # 基础板块恒定携带
    assert "评估窗口" in not_asked and "储蓄目标" in not_asked


def test_context_health_section(db):
    """问健康/评分时带健康评分板块（无快照数据时如实说明数据不足）"""
    _seed_months()
    ctx = coach_service._build_context(USER_A, TODAY, question="我的财务健康吗")
    assert "财务健康评分" in ctx
