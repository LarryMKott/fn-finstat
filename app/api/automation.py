"""自动化（定时任务）接口：任务列表 / 立即执行 / 开关 / 间隔 / 运行历史

挂在 /api/settings/automation 下，属设置页「自动化」分区的数据接口；
任务为全局影响类配置，写操作一律 require_admin（与备份/恢复同策略）。
"""

from fastapi import APIRouter, Depends, Query

from app.api.deps import GatewayUser, get_gateway_user, require_admin
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
from app.services import automation_service

router = APIRouter(prefix="/api/settings/automation", tags=["自动化"])


@router.get(
    "",
    response_model=ApiResponse[AutomationOverview],
    summary="定时任务列表（名称/开关/间隔/上次结果/下次执行时间）",
)
def list_tasks(user: GatewayUser = Depends(get_gateway_user)):
    tasks = [AutomationTask(**t) for t in automation_service.list_tasks()]
    return ok(AutomationOverview(tasks=tasks))


@router.post(
    "/{task_key}/run",
    response_model=ApiResponse[TaskRunResult],
    summary="手动立即执行任务",
)
def run_task(task_key: str, _: GatewayUser = Depends(require_admin)):
    return ok(TaskRunResult(**automation_service.run_task(task_key)))


@router.post(
    "/{task_key}/toggle",
    response_model=ApiResponse[AutomationTask],
    summary="启用/停用任务（停用后不再自动调度，可手动执行）",
)
def toggle_task(
    task_key: str, payload: TaskToggleIn, _: GatewayUser = Depends(require_admin)
):
    return ok(
        AutomationTask(**automation_service.toggle_task(task_key, payload.enabled))
    )


@router.put(
    "/{task_key}",
    response_model=ApiResponse[AutomationTask],
    summary="调整执行间隔（分钟）",
)
def update_task(
    task_key: str, payload: TaskUpdateIn, _: GatewayUser = Depends(require_admin)
):
    return ok(
        AutomationTask(
            **automation_service.update_interval(task_key, payload.interval_minutes)
        )
    )


@router.get(
    "/{task_key}/runs",
    response_model=ApiResponse[TaskRunHistory],
    summary="任务运行历史（最近 N 次，含失败原因）",
)
def list_runs(
    task_key: str,
    limit: int = Query(20, ge=1, le=200),
    user: GatewayUser = Depends(get_gateway_user),
):
    total, rows = automation_service.list_runs(task_key, limit)
    return ok(TaskRunHistory(total=total, runs=[TaskRunOut(**r) for r in rows]))
