"""自动化（定时任务）业务逻辑：查询/开关/间隔/手动执行/运行历史

路由层只做依赖注入与响应包装，DAO 直调收敛到本模块
（与 bill_service / asset_service 的分层惯例一致）。
"""

from app.core.errors import NotFoundError
from app.db.dao import task_dao
from app.services import scheduler


def _get_or_raise(task_key: str) -> dict:
    task = task_dao.TaskDAO.get(task_key)
    if task is None:
        raise NotFoundError("任务不存在")
    return task


def list_tasks() -> list[dict]:
    """任务列表（按注册顺序）"""
    return task_dao.TaskDAO.list_tasks()


def run_task(task_key: str) -> dict:
    """手动立即执行（同步）：走同一套锁与历史记录，供测试与编程调用"""
    _get_or_raise(task_key)
    return scheduler.run_task_now(task_key)


def trigger_task(task_key: str) -> None:
    """手动立即执行（后台异步）：校验与认领在请求内同步完成（未注册/锁
    被占用等错误快速回传用户），任务体在后台线程执行

    nas_watch 单轮最多扫描 2000 个文件，同步执行会把 HTTP 请求挂到网关
    超时；执行结果在运行历史（list_runs）中查看。
    """
    scheduler.start_task(task_key)


def toggle_task(task_key: str, enabled: bool) -> dict:
    """启用/停用：两者均复位失败计数；停用仅摘除调度指针（软锁由执行
    收尾路径按持有者凭证释放，见 TaskDAO.set_enabled）"""
    _get_or_raise(task_key)
    task_dao.TaskDAO.set_enabled(task_key, enabled)
    return _get_or_raise(task_key)


def update_interval(task_key: str, interval_minutes: int) -> dict:
    """调整执行间隔（分钟），下次执行时间按新间隔从当前时刻重排"""
    _get_or_raise(task_key)
    task_dao.TaskDAO.set_interval(task_key, interval_minutes)
    return _get_or_raise(task_key)


def list_runs(task_key: str, limit: int) -> tuple[int, list[dict]]:
    """运行历史（最近 limit 条，含失败原因）"""
    _get_or_raise(task_key)
    return task_dao.TaskDAO.recent_runs(task_key, limit)
