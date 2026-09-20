"""储蓄目标业务逻辑（T-1.4，按当前飞牛账号隔离）

目标 + 结余自动计入 + 进度：进度不是手工存值，而是「目标起始日以来
（收入 − 支出）的累计净结余」，由流水实时计算——记账行为本身推进目标。
与流水不按账本维度（全部账本合并口径），回收站流水自动排除。
"""

from datetime import date

from app.core.errors import NotFoundError, ValidationError
from app.db.dao.savings_dao import SavingsGoalDAO
from app.db.dao.stat_dao import StatDAO
from app.services import audit_service
from app.utils.amount import normalize_amount, round2
from app.utils.period import valid_date as _valid_date

NAME_MAX = 64
NOTE_MAX = 255


def _require_goal(goal_id: int, user_id: str) -> dict:
    goal = SavingsGoalDAO.get(goal_id, user_id)
    if goal is None:
        raise NotFoundError("储蓄目标不存在")
    return goal


def _target_date_or_none(value) -> date | None:
    """历史脏数据兜底：旧版本只验长度，库里可能存着 2026-13-45 这类串；
    进度计算解析失败时不给倒计时口径，而不是打挂整个列表接口"""
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def _progress(goal: dict, today: date) -> dict:
    """结余自动计入：起始日以来的累计净结余（收入 − 支出），来自 bills 实时计算"""
    summary = StatDAO.summary(goal["user_id"], start=goal["start_date"])
    saved = round2(summary["income"] - summary["expense"])
    target = round2(goal["target_amount"])
    remaining = round2(max(target - saved, 0))
    pct = min(100, round(saved / target * 100)) if target > 0 else 0
    done = saved >= target
    result = {
        "saved": saved,
        "remaining": remaining,
        "pct": pct,
        "done": done,
    }
    if goal["target_date"]:
        target_dt = _target_date_or_none(goal["target_date"])
        if target_dt is not None:
            # 目标日之前还需攒够 remaining：给出「所需月均结余」参考口径
            days_left = max((target_dt - today).days, 0)
            months_left = max(days_left / 30.44, 0)  # 月均长度（格里历均值）
            result["months_left"] = round(months_left, 1)
            result["per_month_needed"] = (
                round2(remaining / months_left) if months_left > 0 and not done else 0
            )
    return result


def list_goals(user_id: str, today: date | None = None) -> dict:
    today = today or date.today()
    goals = SavingsGoalDAO.list_goals(user_id)
    items = []
    for g in goals:
        progress = _progress(g, today)
        items.append(
            {
                **{k: v for k, v in g.items() if k != "user_id"},
                **progress,
            }
        )
    return {"items": items}


def create_goal(payload, user_id: str, today: date | None = None) -> dict:
    today = today or date.today()
    name = (payload.name or "").strip()
    if not name:
        raise ValidationError("请填写目标名称")
    target = normalize_amount(payload.target_amount)
    if target <= 0:
        raise ValidationError("目标金额必须大于 0")
    target_date = payload.target_date or None
    if target_date:
        target_date = _valid_date(target_date, "目标日期")
    goal = SavingsGoalDAO.create(
        user_id,
        {
            "name": name[:NAME_MAX],
            "target_amount": target,
            "start_date": today.isoformat(),  # 创建日起的结余自动计入
            "target_date": target_date,
            "note": (payload.note or "").strip()[:NOTE_MAX],
        },
    )
    result = {
        **{k: v for k, v in goal.items() if k != "user_id"},
        **_progress(goal, today),
    }
    return result


def update_goal(goal_id: int, payload, user_id: str, today: date | None = None) -> dict:
    today = today or date.today()
    _require_goal(goal_id, user_id)
    fields: dict = {}
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise ValidationError("请填写目标名称")
        fields["name"] = name[:NAME_MAX]
    if payload.target_amount is not None:
        target = normalize_amount(payload.target_amount)
        if target <= 0:
            raise ValidationError("目标金额必须大于 0")
        fields["target_amount"] = target
    if payload.target_date is not None:
        fields["target_date"] = (
            _valid_date(payload.target_date, "目标日期")
            if payload.target_date
            else None
        )
    if payload.note is not None:
        fields["note"] = payload.note.strip()[:NOTE_MAX]
    updated = SavingsGoalDAO.update_fields(goal_id, user_id, fields)
    if updated is None:
        raise NotFoundError("储蓄目标不存在")
    audit_service.record(
        user_id,
        "savings.update",
        "savings_goal",
        goal_id,
        "调整储蓄目标「"
        + updated["name"]
        + "」→ "
        + str(updated["target_amount"])
        + " 元",
    )
    return {
        **{k: v for k, v in updated.items() if k != "user_id"},
        **_progress(updated, today),
    }


def delete_goal(goal_id: int, user_id: str) -> None:
    goal = _require_goal(goal_id, user_id)
    if not SavingsGoalDAO.delete(goal_id, user_id):
        raise NotFoundError("储蓄目标不存在")
    audit_service.record(
        user_id,
        "savings.delete",
        "savings_goal",
        goal_id,
        "删除储蓄目标「" + goal["name"] + "」",
    )
