"""自动化（定时任务）接口模型"""

from pydantic import BaseModel, Field


class AutomationTask(BaseModel):
    """任务定义（设置页「自动化」列表行）"""

    task_key: str
    name: str
    enabled: bool
    interval_minutes: int
    next_run_at: float | None = None
    running_at: float | None = None
    failure_count: int = 0
    last_status: str = ""
    last_message: str = ""


class AutomationOverview(BaseModel):
    """设置页「自动化」分区总览"""

    tasks: list[AutomationTask]


class TaskRunOut(BaseModel):
    """任务运行历史条目"""

    id: int
    task_key: str
    started_at: float
    finished_at: float
    ok: bool
    error: str = ""
    affected: int = 0


class TaskRunHistory(BaseModel):
    """运行历史（含总数，供分页/统计）"""

    total: int
    runs: list[TaskRunOut]


class TaskToggleIn(BaseModel):
    enabled: bool


class TaskUpdateIn(BaseModel):
    interval_minutes: int = Field(ge=1, le=60 * 24 * 7, description="执行间隔（分钟）")


class TaskRunResult(BaseModel):
    """手动立即执行的返回"""

    task_key: str
    ok: bool
    affected: int = 0
    message: str = ""
    disabled: bool = False
