"""自动化（定时任务）接口：任务列表 / 立即执行 / 开关 / 间隔 / 运行历史

挂在 /api/settings/automation 下，属设置页「自动化」分区的数据接口；
任务为全局影响类配置，写操作一律 require_admin（与备份/恢复同策略）。
"""

from fastapi import APIRouter, Depends, Query

from app.api.deps import AdminUser, CurrentUser, request_db_session
from app.schemas.automation import (
    AutomationOverview,
    AutomationTask,
    TaskRunHistory,
    TaskRunOut,
    TaskRunResult,
    TaskToggleIn,
    TaskUpdateIn,
)
from app.schemas.common import ApiResponse, ok
from app.services import audit_service, automation_service

router = APIRouter(
    prefix="/api/settings/automation",
    tags=["自动化"],
    dependencies=[Depends(request_db_session)],
)


@router.get(
    "",
    response_model=ApiResponse[AutomationOverview],
    summary="定时任务列表（名称/开关/间隔/上次结果/下次执行时间）",
)
def list_tasks(user: CurrentUser):
    # 读接口对网关所有登录用户开放（写操作才 require_admin）；
    # last_message/error 经调度器 redact_paths 脱敏入库，不泄漏服务器路径
    tasks = [AutomationTask(**t) for t in automation_service.list_tasks()]
    return ok(AutomationOverview(tasks=tasks))


@router.post(
    "/{task_key}/run",
    response_model=ApiResponse[TaskRunResult],
    summary="手动立即执行任务（后台异步执行，结果在运行历史中查看）",
)
def run_task(user: AdminUser, task_key: str):
    # 校验与认领同步完成（未注册 404 / 锁被占用 400 快速回传用户），
    # 任务体在后台线程执行：nas_watch 单轮最多扫 2000 个文件，
    # 同步执行会把请求挂到网关超时
    automation_service.trigger_task(task_key)
    audit_service.record(
        user.user_id,
        "automation.run",
        "automation",
        task_key,
        "手动触发任务 " + task_key,
    )
    return ok(
        TaskRunResult(
            task_key=task_key,
            ok=True,
            message="已触发执行，请稍后在运行历史中查看结果",
        )
    )


@router.post(
    "/{task_key}/toggle",
    response_model=ApiResponse[AutomationTask],
    summary="启用/停用任务（停用后不再自动调度，可手动执行）",
)
def toggle_task(user: AdminUser, task_key: str, payload: TaskToggleIn):
    updated = automation_service.toggle_task(task_key, payload.enabled)
    audit_service.record(
        user.user_id,
        "automation.toggle",
        "automation",
        task_key,
        ("启用" if payload.enabled else "停用") + "任务 " + task_key,
    )
    return ok(AutomationTask(**updated))


@router.put(
    "/{task_key}",
    response_model=ApiResponse[AutomationTask],
    summary="调整执行间隔（分钟）",
)
def update_task(user: AdminUser, task_key: str, payload: TaskUpdateIn):
    updated = automation_service.update_interval(task_key, payload.interval_minutes)
    audit_service.record(
        user.user_id,
        "automation.interval",
        "automation",
        task_key,
        "调整任务间隔 → " + str(payload.interval_minutes) + " 分钟",
    )
    return ok(AutomationTask(**updated))


@router.get(
    "/{task_key}/runs",
    response_model=ApiResponse[TaskRunHistory],
    summary="任务运行历史（最近 N 次，含失败原因）",
)
def list_runs(user: CurrentUser, task_key: str, limit: int = Query(20, ge=1, le=200)):
    # 与 list_tasks 同策略：只读展示，error 字段已经调度器脱敏
    total, rows = automation_service.list_runs(task_key, limit)
    return ok(TaskRunHistory(total=total, runs=[TaskRunOut(**r) for r in rows]))
