"""月度报告自动生成（AI-4）测试：开关口径 / 幂等跳过 / 失败通知去重 / 费用计数

口径钉住脑洞清单 §7.3 的用户决策：默认开、每月生成上月报告、
生成失败只提醒不让调度任务失败、本月生成次数对用户可见。
LLM 调用经 monkeypatch 替换，单测不发起真实网络请求。
"""

from datetime import date

import pytest

from app.db.dao.ai_report_dao import AIReportDAO
from app.db.dao.bill_dao import BillDAO
from app.db.dao.notify_dao import NotificationDAO
from app.file_settings import AISettings, load_ai_settings
from app.services import ai_service, auto_report_service
from tests.conftest import USER_A, USER_B, make_bill_records

TODAY = date(2026, 9, 15)  # 月中执行：目标月恒为上月 2026-08
TARGET_MONTH = "2026-08"


@pytest.fixture(autouse=True)
def state_file_isolated(tmp_path, monkeypatch):
    """自动报告计数状态文件指向临时路径（与 conftest 的配置文件隔离同策略）"""
    monkeypatch.setattr(
        auto_report_service, "STATE_FILE", tmp_path / "auto_report_state.json"
    )


def _ready(monkeypatch, enabled=True):
    """让开关/密钥检查通过（不读写真实 ai_config.json）"""
    monkeypatch.setattr(
        auto_report_service,
        "load_ai_settings",
        lambda: AISettings(api_key="sk-test", auto_report_enabled=enabled),
    )


def _fake_generate(monkeypatch) -> list[tuple[str, str, str]]:
    """替换报告生成为无网络假实现，返回调用记录供断言（防真实计费）"""
    calls: list[tuple[str, str, str]] = []

    def fake(user_id, period_type, period_value):
        calls.append((user_id, period_type, period_value))
        return {
            "period_type": period_type,
            "period_value": period_value,
            "title": f"{period_value} 消费分析报告",
            "report": f"# {period_value} 报告",
            "context": {"period_type": period_type, "period_value": period_value},
        }

    monkeypatch.setattr(ai_service, "generate_report", fake)
    return calls


def _seed_bills(user_id: str, prefix: str, month: str = TARGET_MONTH) -> None:
    BillDAO.insert_many(
        make_bill_records(
            1, prefix=prefix, tx_time=f"{month}-10 10:00:00", amount=10.0
        ),
        user_id,
    )


def test_default_toggle_is_open():
    """决策 §7.3：自动报告默认开启（存量配置文件无此键时同样按开处理）"""
    assert load_ai_settings().auto_report_enabled is True


def test_disabled_skips_without_ai_call(monkeypatch):
    """开关关闭：静默跳过，不产生任何 AI 调用与归档"""
    _ready(monkeypatch, enabled=False)
    calls = _fake_generate(monkeypatch)
    affected, message = auto_report_service.monthly_generate(TODAY)
    assert affected == 0 and "已关闭" in message
    assert calls == []


def test_not_configured_skips(monkeypatch):
    """未配置密钥：静默跳过（能走到密钥检查即证明默认开关为开）"""
    calls = _fake_generate(monkeypatch)
    affected, message = auto_report_service.monthly_generate(TODAY)
    assert affected == 0 and "未配置 AI" in message
    assert calls == []


def test_generates_archives_counts_and_is_idempotent(db, monkeypatch):
    """正常链路：逐账号生成+归档+计数；重复执行命中归档闸门不再计费"""
    _ready(monkeypatch)
    _seed_bills(USER_A, "AR-A")
    _seed_bills(USER_B, "AR-B")
    calls = _fake_generate(monkeypatch)

    affected, message = auto_report_service.monthly_generate(TODAY)
    assert affected == 2 and "生成 2 份" in message
    assert sorted(calls) == [
        (USER_A, "month", TARGET_MONTH),
        (USER_B, "month", TARGET_MONTH),
    ]
    for user_id in (USER_A, USER_B):
        archived = AIReportDAO.get_by_scope(user_id, "month", TARGET_MONTH)
        assert archived is not None and archived["content"].startswith("# ")
    assert auto_report_service.monthly_generated(TODAY) == 2

    # 同月再次执行（手动触发/次日调度）：已归档即跳过，不重复调用与计数
    affected2, message2 = auto_report_service.monthly_generate(TODAY)
    assert affected2 == 0 and "已有归档跳过 2 个" in message2
    assert len(calls) == 2
    assert auto_report_service.monthly_generated(TODAY) == 2


def test_manual_archive_prevents_regeneration(db, monkeypatch):
    """用户已手动生成并归档上月报告：自动任务不再重复生成"""
    _ready(monkeypatch)
    _seed_bills(USER_A, "AR-MANUAL")
    ai_service.archive_report(
        user_id=USER_A,
        period_type="month",
        period_value=TARGET_MONTH,
        title="手动生成的报告",
        content="# 手动内容",
        stats_summary=None,
    )
    calls = _fake_generate(monkeypatch)
    affected, message = auto_report_service.monthly_generate(TODAY)
    assert affected == 0 and "已有归档跳过 1 个" in message
    assert calls == []


def test_empty_month_skipped_without_ai_call(db, monkeypatch):
    """目标月无任何收支的账号跳过：空月报告没有信息量，纯耗 API 费用"""
    _ready(monkeypatch)
    _seed_bills(USER_A, "AR-OLD", month="2026-07")  # 有流水但不在目标月
    calls = _fake_generate(monkeypatch)
    affected, message = auto_report_service.monthly_generate(TODAY)
    assert affected == 0 and "当月无流水跳过 1 个" in message
    assert calls == []
    assert auto_report_service.monthly_generated(TODAY) == 0


def test_failure_notifies_once_and_task_stays_ok(db, monkeypatch):
    """生成失败：只提醒不抛错（避免调度退避停用），同月同账号至多提醒一次"""
    _ready(monkeypatch)
    _seed_bills(USER_A, "AR-FAIL")

    def boom(user_id, period_type, period_value):
        raise ai_service.AIClientError("接口返回 500：服务过载")

    monkeypatch.setattr(ai_service, "generate_report", boom)

    affected, message = auto_report_service.monthly_generate(TODAY)
    assert affected == 0 and "失败 1 个" in message
    # 同月重复执行失败：task_failed 按 账号+目标月 去重，不逐日刷屏
    auto_report_service.monthly_generate(TODAY)
    events = NotificationDAO.list_for_user(USER_A, limit=50)
    failed = [e for e in events if e["event_type"] == "task_failed"]
    assert len(failed) == 1 and "500" in failed[0]["content"]
    # 失败不留半成品归档：修复后次日重试能正常生成
    assert AIReportDAO.get_by_scope(USER_A, "month", TARGET_MONTH) is None


def test_counter_resets_next_month(db, monkeypatch):
    """费用计数按月滚动：跨月后归零（展示口径为「本月」）"""
    _ready(monkeypatch)
    _seed_bills(USER_A, "AR-CNT")
    _fake_generate(monkeypatch)
    auto_report_service.monthly_generate(TODAY)
    assert auto_report_service.monthly_generated(TODAY) == 1
    assert auto_report_service.monthly_generated(date(2026, 10, 2)) == 0
