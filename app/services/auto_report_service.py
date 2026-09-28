"""月度报告自动生成（AI-4，脑洞清单 §7.3 落定口径）

产品定位：AI 报告从「用户手点、等待数十秒」变为「月初自动生成、只看通知」。
复用已交付的 generate_report（数字后端算好、模型只解释）+ 归档 upsert +
notify_report_ready 事件（报告就绪通知链路现成），本模块只做调度编排。

落定口径（2026-09-20 决策 §7.3，勿随意调整）：
- **默认开启**，设置页 AI 卡片可关（ai_config.json 的 auto_report_enabled，
  关闭后不再自动生成，手动生成不受影响）
- **每月 1 日生成上月报告**：调度底座为固定间隔制，实现为「每日一查，上月
  报告缺归档才生成」——首次满足条件多发生在 1 日；天然幂等（已归档即跳过、
  不重复计费），停机错过补跑合并为一次，恢复后首个 tick 补上
- **生成失败只写日志 + 发 task_failed 通知**（按 账号+目标月 去重，每日重试
  至多提醒一次）；不让任务整体抛错——AI 服务临时故障不应触发调度退避把任务
  连续失败停用，次日重试、次月自然恢复
- **费用可见性**：成功生成即累加计数（DATA_DIR 状态文件，按月滚动清零），
  设置页 AI 卡片展示「本月已自动生成 N 份」，被动产生的费用对用户透明

多账号逐账号独立生成；当月无任何收支的账号跳过（空月报告没有信息量，
纯耗 API 费用）。
"""

import logging
from datetime import date

from app.config import DATA_DIR, read_json_config, write_json_config
from app.db.dao.ai_report_dao import AIReportDAO
from app.db.dao.bill_dao import BillDAO
from app.db.dao.stat_dao import StatDAO
from app.file_settings import load_ai_settings
from app.services import ai_service, notify_service
from app.utils.period import period_range, prev_month

logger = logging.getLogger(__name__)

TASK_KEY = "report_monthly"
# 每日一查：仅当发现上月报告缺归档时才产生 AI 调用，其余日子为只读幂等跳过
TASK_INTERVAL_MINUTES = 24 * 60

# 本月自动生成计数的状态文件（非用户配置，由任务自身维护；按月滚动清零）
STATE_FILE = DATA_DIR / "auto_report_state.json"


def _current_month(today: date) -> str:
    return f"{today.year:04d}-{today.month:02d}"


def _bump_counter(today: date, count: int) -> None:
    """累加本月自动生成计数：跨月首写自动清零"""
    state = read_json_config(STATE_FILE, "自动报告")
    cur = _current_month(today)
    if state.get("month") != cur:
        state = {"month": cur, "count": 0}
    state["count"] = int(state.get("count") or 0) + count
    write_json_config(STATE_FILE, state)


def monthly_generated(today: date | None = None) -> int:
    """本月已自动生成的报告份数（设置页 AI 卡片的费用可见性展示）"""
    today = today or date.today()
    state = read_json_config(STATE_FILE, "自动报告")
    if state.get("month") != _current_month(today):
        return 0
    return int(state.get("count") or 0)


def monthly_generate(today: date | None = None) -> tuple[int, str]:
    """周期任务入口（scheduler 契约）：为所有账号补齐上月归档报告

    返回 (生成份数, 摘要)。调度路径与自动化页「立即执行」共用：
    开关关闭 / 未配置密钥为静默跳过（不产生失败记录），单账号失败不影响
    其他账号，全部失败也不抛错（理由见模块 docstring）。
    """
    today = today or date.today()
    settings = load_ai_settings()
    if not settings.auto_report_enabled:
        return 0, "自动生成已关闭，跳过"
    if not settings.ready:
        return 0, "未配置 AI 服务密钥，跳过"

    target_month = prev_month(_current_month(today))
    start, end = period_range("month", target_month)
    generated = skipped_archived = skipped_empty = failed = 0
    for user_id in BillDAO.distinct_user_ids():
        # 幂等闸门：上月报告已在档（含用户手动生成过）即跳过，不重复计费
        if AIReportDAO.get_by_scope(user_id, "month", target_month) is not None:
            skipped_archived += 1
            continue
        summary = StatDAO.summary(user_id, start=start, end=end)
        if not summary["income"] and not summary["expense"]:
            skipped_empty += 1
            continue
        try:
            result = ai_service.generate_report(user_id, "month", target_month)
            ai_service.archive_report(
                user_id=user_id,
                period_type="month",
                period_value=target_month,
                title=result["title"],
                content=result["report"],
                stats_summary=result["context"],
            )
        except Exception as exc:
            # 失败不外抛（避免调度退避停用）；按 账号+目标月 去重提醒，次日重试
            failed += 1
            logger.warning(
                "账号 %s 自动生成 %s 月度报告失败：%s", user_id, target_month, exc
            )
            notify_service.create_event(
                "task_failed",
                f"{target_month} 月度报告自动生成失败",
                f"自动生成报告未成功：{exc}。修复后可在 AI 报告面板手动生成，"
                "任务将于次日自动重试。",
                user_id=user_id,
                dedup_key=f"auto_report:{user_id}:{target_month}",
            )
            continue
        generated += 1
    if generated:
        _bump_counter(today, generated)
    return generated, (
        f"目标月 {target_month}：生成 {generated} 份，已有归档跳过 {skipped_archived} 个、"
        f"当月无流水跳过 {skipped_empty} 个账号，失败 {failed} 个"
    )
