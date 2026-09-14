"""进程内定时任务调度器（T-5.1 定时任务底座）

设计要点（见 docs/devlog 开发计划，不引入 APScheduler）：
- 生产为单进程 uvicorn（cmd/main 无 --workers），进程内 asyncio 循环足够；
  依赖极简对 fnOS 离线安装有实际意义
- 四项必备行为：
  1. 错过补跑（catch-up）：停机期间错过的执行合并为一次，启动后延迟
     STARTUP_DELAY 秒的首个 tick 补跑（run_due_tasks 对过期任务只执行一次，
     成功后按间隔重排，天然合并、不逐次补）
  2. 并发互斥：同一任务不并发，TaskDAO.try_claim 抢 running_at 软锁并返回
     TaskLock 句柄，超时（LOCK_TIMEOUT）自动解锁防死锁；收尾经句柄按
     持有者凭证释放，锁超时被重认领后旧执行者不会误清新持有者的状态
  3. 失败退避：连续失败按 1/2/4 倍间隔退避，连续 MAX_FAILURES 次自动停用
  4. 运行历史：每次执行写 task_runs（起止/结果/错误摘要/影响条数），
     超出保留窗口的旧记录由 TaskDAO.add_run 顺手清理
- 已知边界：仅单进程单实例下成立；表结构预留 locked_by/locked_until
  作为多实例升级路径

任务实现契约：注册的 callable 无参、同步（在线程池中执行）、内部自行读取配置；
返回 (affected:int, message:str)，失败抛异常即可。
"""

import asyncio
import logging
import os
import re
import threading
import time
from typing import Callable, Optional

from app.core.errors import NotFoundError, ValidationError
from app.db.dao import task_dao
from app.db.dao.task_dao import MAX_FAILURES, TaskLock

logger = logging.getLogger(__name__)

# 调度循环参数
TICK_SECONDS = 30  # 扫描到期任务的周期
STARTUP_DELAY = 60  # 启动后延迟再首个 tick（错过补跑合并为一次的落点）

# 任务注册表：task_key -> (名称, 默认间隔分钟, 执行函数)
# 用函数注册而非装饰器收集，避免模块导入顺序影响注册结果
_REGISTRY: dict[str, dict] = {}

# 绝对路径形态（Windows 盘符 / POSIX 至少一级目录）；用于错误摘要脱敏
_PATH_TOKEN_RE = re.compile(
    r"[A-Za-z]:[\\/][^\s'\"<>|*?]*"  # Windows 盘符绝对路径
    r"|(?<![:/\w])/(?:[\w.\-]+/)+[\w.\-]*"  # POSIX 绝对路径（至少一级目录）
)


def redact_paths(text: str) -> str:
    """错误/摘要脱敏：绝对文件路径只保留文件名

    任务列表与运行历史对网关下所有登录用户可见（写操作才限管理员），
    而 OSError 类异常文本携带完整服务器目录路径，不能原样入库展示。
    """

    def _keep_basename(match: re.Match) -> str:
        basename = match.group(0).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        return basename or match.group(0)

    return _PATH_TOKEN_RE.sub(_keep_basename, text)


def register_task(
    task_key: str, name: str, interval_minutes: int, fn: Callable
) -> None:
    """注册任务实现（幂等；初始化时调用 ensure_builtin_tasks 落库）"""
    _REGISTRY[task_key] = {
        "name": name,
        "interval_minutes": interval_minutes,
        "fn": fn,
    }


def ensure_builtin_tasks() -> None:
    """把注册表落库（幂等）：已存在的任务不覆盖用户修改过的开关/间隔"""
    for key, spec in _REGISTRY.items():
        task_dao.TaskDAO.ensure_task(key, spec["name"], spec["interval_minutes"])


def run_due_tasks(now: Optional[float] = None) -> list[dict]:
    """执行所有到期任务（同步，测试与调度循环共用）：返回每次执行的摘要

    单次扫描内每个任务最多执行一次 —— 停机错过 N 个周期也只补跑 1 次。
    """
    now = time.time() if now is None else now
    summaries = []
    for task_key in task_dao.TaskDAO.due_task_keys(now):
        lock = task_dao.TaskDAO.try_claim(task_key, now)
        if lock is None:
            logger.info("任务 %s 正在运行，本次跳过", task_key)
            summaries.append(
                {
                    "task_key": task_key,
                    "ok": True,
                    "affected": 0,
                    "message": "正在运行中，跳过",
                    "disabled": False,
                }
            )
            continue
        summaries.append(_execute(task_key, now, lock))
    return summaries


def _claim_manual(task_key: str, now: float) -> TaskLock:
    """手动执行的前置校验与认领（同步、快速失败，供接口层回传用户）"""
    if task_dao.TaskDAO.get(task_key) is None:
        raise NotFoundError("任务不存在")
    if _REGISTRY.get(task_key) is None:
        # 必须在认领之前判掉：认领后提前 return 无人释放软锁，
        # 该任务会假死到 LOCK_TIMEOUT 超时（最长 30 分钟）
        raise NotFoundError("任务未注册，无法执行")
    lock = task_dao.TaskDAO.try_claim(task_key, now, require_enabled=False)
    if lock is None:
        raise ValidationError("任务正在运行中，请稍后再试")
    return lock


def run_task_now(task_key: str, now: Optional[float] = None) -> dict:
    """手动立即执行（同步）：走同一套锁与历史记录，供测试与编程调用

    接口语义承诺「停用后不再自动调度，可手动执行」，因此认领时不要求
    enabled —— 调度路径 try_claim 默认要求启用，这里显式放开。
    """
    now = time.time() if now is None else now
    lock = _claim_manual(task_key, now)
    return _execute(task_key, now, lock)


def start_task(task_key: str) -> None:
    """手动触发（异步）：校验与认领同步完成，任务体在后台线程执行

    nas_watch 单轮最多扫描 2000 个文件，同步执行会把 HTTP 请求挂到网关
    超时；认领仍在请求内完成（未注册/锁被占用等错误同步抛给用户），
    执行结果在运行历史中查看。
    """
    now = time.time()
    lock = _claim_manual(task_key, now)

    def _run() -> None:
        try:
            _execute(task_key, now, lock)
        except Exception:  # 后台线程异常只记日志，线程不得带异常退出
            logger.exception("手动触发任务 %s 执行失败", task_key)

    threading.Thread(
        target=_run, name=f"fn-finstat-task-{task_key}", daemon=True
    ).start()


def _execute(task_key: str, now: float, lock: TaskLock) -> dict:
    """执行单个任务并记录历史与调度状态（异常不外泄）

    认领由调用方完成后传入锁句柄；收尾路径若自身抛异常（如 DB 瞬断），
    由 except 兜底经句柄释放软锁 —— 句柄的 UPDATE 带认领时间戳条件，
    只会清自己那次锁。
    """
    spec = _REGISTRY.get(task_key)
    if spec is None:
        # 认领路径已校验注册，正常不可达；防御性释放避免锁悬挂
        logger.error("任务 %s 无注册实现，跳过", task_key)
        lock.release()
        return {
            "task_key": task_key,
            "ok": False,
            "affected": 0,
            "message": "任务未注册",
            "disabled": False,
        }
    started_at = now  # 调度时钟为基准（测试可注入），耗时另用单调钟补足
    ok, error, affected, message = True, "", 0, ""
    t0 = time.perf_counter()
    try:
        result = spec["fn"]()
        affected, message = int(result[0]), redact_paths(str(result[1]))
    except Exception as exc:  # 任务失败绝不影响其他任务与主流程
        ok, error = False, redact_paths(f"{type(exc).__name__}: {exc}")
        logger.warning("定时任务 %s 执行失败：%s", task_key, error)
    finished_at = now + (time.perf_counter() - t0)
    disabled = False
    try:
        task_dao.TaskDAO.add_run(task_key, started_at, finished_at, ok, error, affected)
        if ok:
            lock.record_success(finished_at, message)
        else:
            disabled = lock.record_failure(finished_at, error)
            if disabled:
                logger.warning(
                    "任务 %s 连续失败 %d 次，已自动停用", task_key, MAX_FAILURES
                )
    except Exception as exc:
        # 执行本身已完成，只是记录失败：兜底释放软锁，返回失败摘要（不外抛）
        logger.exception("任务 %s 记录执行结果失败", task_key)
        try:
            lock.release()
        except Exception:
            logger.exception("任务 %s 软锁兜底释放失败，将等待锁超时自动解锁", task_key)
        return {
            "task_key": task_key,
            "ok": False,
            "affected": affected,
            "message": f"执行完成但结果记录失败：{redact_paths(str(exc))}",
            "disabled": False,
        }
    return {
        "task_key": task_key,
        "ok": ok,
        "affected": affected,
        "message": message or error,
        "disabled": disabled,
    }


# ---- 进程内调度循环（仅生产启动；测试直接调 run_due_tasks，不经此路径） ----

_loop_task: Optional[asyncio.Task] = None


def scheduler_enabled() -> bool:
    """环境开关：默认启用；单测环境经 conftest 置 0 关闭（避免测试期间触发任务）"""
    return os.getenv("SCHEDULER_ENABLED", "1").strip().lower() not in (
        "0",
        "false",
        "off",
    )


async def _loop() -> None:
    logger.info(
        "定时任务调度器启动（首个 tick 延迟 %ds，周期 %ds）",
        STARTUP_DELAY,
        TICK_SECONDS,
    )
    await asyncio.sleep(STARTUP_DELAY)
    while True:
        try:
            await asyncio.to_thread(run_due_tasks)
        except asyncio.CancelledError:
            raise
        except Exception:  # 循环自身的意外异常只记日志，不终止调度
            logger.exception("调度 tick 异常（不影响后续调度）")
        await asyncio.sleep(TICK_SECONDS)


def start_loop() -> None:
    """在运行中的事件循环里启动调度协程（main.lifespan 启动时调用）"""
    global _loop_task
    if _loop_task is not None and not _loop_task.done():
        return
    _loop_task = asyncio.get_running_loop().create_task(_loop())


async def stop_loop() -> None:
    global _loop_task
    if _loop_task is not None:
        _loop_task.cancel()
        try:
            await _loop_task
        except asyncio.CancelledError:
            pass
        _loop_task = None
