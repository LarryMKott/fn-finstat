"""自动化底座数据访问层：定时任务定义/运行历史 + 已导入文件登记

任务调度状态机（详见 docs/devlog 开发计划 T-5.1）：
- 认领：try_claim 以原子 UPDATE 抢占 running_at 软锁，同任务不并发；
  超过 LOCK_TIMEOUT 未释放的锁视为死锁，可被重新认领
- 成功：failure_count 清零，next_run_at = now + interval
- 失败：按 1/2/4 倍间隔退避，连续 MAX_FAILURES 次失败自动停用
"""

import hashlib
import time
from typing import Optional

from sqlalchemy import func, select, update

from app.db.base import get_db, translate_unique_violation
from app.db.models import ImportedFile, ScheduledTask, TaskRun

# 软锁超时（秒）：任务执行超过该时长仍未释放锁，视为死锁可被重新认领
LOCK_TIMEOUT = 30 * 60
# 连续失败自动停用的阈值（与调度器 BACKOFF 配合，调度器引用同一常量）
MAX_FAILURES = 5
# 连续失败的间隔退避倍数（调度器日志口径与这里共用同一份，防止两处漂移）
BACKOFF_MULTIPLIERS = (1, 2, 4)


def path_key_of(path: str) -> str:
    """文件路径的唯一键（sha256 摘要）：路径任意长且跨方言索引安全"""
    return hashlib.sha256(path.replace("\\", "/").encode("utf-8")).hexdigest()


class TaskDAO:
    @staticmethod
    def ensure_task(task_key: str, name: str, interval_minutes: int) -> None:
        """注册任务（幂等）：已存在时不覆盖用户的开关与间隔设置"""
        with get_db() as session:
            exists = session.scalar(
                select(ScheduledTask.id).where(ScheduledTask.task_key == task_key)
            )
            if exists is not None:
                return
            with translate_unique_violation("任务已存在"):
                session.add(
                    ScheduledTask(
                        task_key=task_key,
                        name=name,
                        interval_minutes=interval_minutes,
                        next_run_at=None,
                        updated_at=time.time(),
                    )
                )
                # flush 让唯一约束在上下文管理器内立即触发，
                # 否则 IntegrityError 抛在 commit 处、翻译上下文已退出（对齐 add_run 写法）
                session.flush()

    @staticmethod
    def list_tasks() -> list[dict]:
        with get_db() as session:
            stmt = select(ScheduledTask).order_by(ScheduledTask.id)
            return [t.as_dict() for t in session.scalars(stmt)]

    @staticmethod
    def get(task_key: str) -> Optional[dict]:
        with get_db() as session:
            row = session.scalar(
                select(ScheduledTask).where(ScheduledTask.task_key == task_key)
            )
            return row.as_dict() if row is not None else None

    @staticmethod
    def set_enabled(task_key: str, enabled: bool) -> None:
        """启用时清零失败计数；停用时清空下次执行时间与软锁

        启用分支刻意不动 next_run_at / running_at：
        - next_run_at 保持原值（停用时已置 None，启用后最近一个 tick 就会执行）
        - running_at 绝不能清 —— 若任务正在执行中被停用又立刻启用，清锁会让
          下一次认领成功，同一任务并发跑两遍（运行结束路径会安全释放锁）
        """
        values: dict = {
            "enabled": enabled,
            "failure_count": 0,
            "updated_at": time.time(),
        }
        if not enabled:
            values.update(
                next_run_at=None, running_at=None, locked_by="", locked_until=None
            )
        with get_db() as session:
            session.execute(
                update(ScheduledTask)
                .where(ScheduledTask.task_key == task_key)
                .values(**values)
            )

    @staticmethod
    def set_interval(task_key: str, interval_minutes: int) -> None:
        """调整执行间隔；下次执行时间按新间隔从当前时刻重排"""
        with get_db() as session:
            session.execute(
                update(ScheduledTask)
                .where(ScheduledTask.task_key == task_key)
                .values(
                    interval_minutes=interval_minutes,
                    next_run_at=time.time() + interval_minutes * 60,
                    updated_at=time.time(),
                )
            )

    @staticmethod
    def try_claim(task_key: str, now: float, require_enabled: bool = True) -> bool:
        """原子认领软锁：任务空闲（或锁已超时）时置 running_at，返回是否抢到

        require_enabled=False 供「手动立即执行」使用 —— 接口语义承诺停用的任务
        仍可手动触发（调度路径保持 True，停用即不再自动执行）。
        """
        conds = [ScheduledTask.task_key == task_key]
        if require_enabled:
            conds.append(ScheduledTask.enabled.is_(True))
        conds.append(
            (ScheduledTask.running_at.is_(None))
            | (ScheduledTask.running_at < now - LOCK_TIMEOUT)
        )
        with get_db() as session:
            rowcount = session.execute(
                update(ScheduledTask)
                .where(*conds)
                .values(
                    running_at=now, locked_by="local", locked_until=now + LOCK_TIMEOUT
                )
            ).rowcount
        return rowcount > 0

    @staticmethod
    def release(task_key: str) -> None:
        """无条件释放软锁（仅清锁字段，不碰调度状态）

        常规释放口在 record_success / record_failure 的 UPDATE 里；
        本方法只服务于异常兜底路径 —— 记录历史/调度状态时抛异常，
        锁不能一直挂到 LOCK_TIMEOUT 超时才解开（那是最长 30 分钟的假死）。
        """
        with get_db() as session:
            session.execute(
                update(ScheduledTask)
                .where(ScheduledTask.task_key == task_key)
                .values(running_at=None, locked_by="", locked_until=None)
            )

    @staticmethod
    def record_success(task_key: str, now: float, message: str = "") -> None:
        """执行成功：清零失败计数、按间隔重排下次执行、释放锁（单条 UPDATE）"""
        with get_db() as session:
            session.execute(
                update(ScheduledTask)
                .where(ScheduledTask.task_key == task_key)
                .values(
                    failure_count=0,
                    next_run_at=now + ScheduledTask.interval_minutes * 60,
                    running_at=None,
                    locked_by="",
                    locked_until=None,
                    last_status="ok",
                    last_message=message[:500],
                    updated_at=now,
                )
            )

    @staticmethod
    def record_failure(task_key: str, now: float, message: str) -> bool:
        """执行失败：按 1/2/4 倍间隔退避；连续 MAX_FAILURES 次失败自动停用

        返回是否触发了自动停用（调用方据此产生通知事件）。

        并发安全：failure_count 用「数据库端原子自增」而非先读后写，与 try_claim
        的原子 UPDATE 风格保持一致 —— 若并发触发（手动「立即执行」与调度 tick 撞上），
        先读后写会丢失计数并使自动停用阈值失效。
        """
        backoff_multiplier = BACKOFF_MULTIPLIERS
        with get_db() as session:
            # 第一步：原子自增并释放锁；同时取出 interval_minutes 供退避计算
            rowcount = session.execute(
                update(ScheduledTask)
                .where(ScheduledTask.task_key == task_key)
                .values(
                    failure_count=ScheduledTask.failure_count + 1,
                    running_at=None,
                    locked_by="",
                    locked_until=None,
                    updated_at=now,
                )
            ).rowcount
            if rowcount == 0:
                return False  # 任务不存在
            # 第二步：同事务读回自增后的真实值（原子自增的结果只有数据库知道）
            task = session.scalar(
                select(ScheduledTask).where(ScheduledTask.task_key == task_key)
            )
            if task is None:  # 理论上不可达（同事务内刚更新过）
                return False
            failure_count = task.failure_count
            disable = failure_count >= MAX_FAILURES
            values = {
                "last_status": "disabled" if disable else "failed",
                "last_message": message[:500],
            }
            if disable:
                values["enabled"] = False
                values["next_run_at"] = None
            else:
                multiplier = backoff_multiplier[min(failure_count - 1, 2)]
                values["next_run_at"] = now + task.interval_minutes * 60 * multiplier
            session.execute(
                update(ScheduledTask)
                .where(ScheduledTask.task_key == task_key)
                .values(**values)
            )
        return disable

    @staticmethod
    def due_task_keys(now: float) -> list[str]:
        """到期待执行的任务 key（启用且 next_run_at 为空或已到期）"""
        with get_db() as session:
            stmt = select(ScheduledTask.task_key).where(
                ScheduledTask.enabled.is_(True),
                (ScheduledTask.next_run_at.is_(None))
                | (ScheduledTask.next_run_at <= now),
            )
            return list(session.scalars(stmt))

    @staticmethod
    def add_run(
        task_key: str,
        started_at: float,
        finished_at: float,
        ok: bool,
        error: str = "",
        affected: int = 0,
    ) -> int:
        with get_db() as session:
            run = TaskRun(
                task_key=task_key,
                started_at=started_at,
                finished_at=finished_at,
                ok=ok,
                error=error[:500],
                affected=affected,
            )
            session.add(run)
            session.flush()
            return run.id

    @staticmethod
    def recent_runs(task_key: str, limit: int = 20) -> tuple[int, list[dict]]:
        """最近 N 次运行历史（倒序）+ 总条数

        返回 (total, runs) 元组：总条数单独返回，避免把元信息混进记录列表
        （原实现返回 [{"total": n}, *runs]，调用方须约定首元素是元信息，
        任何按直觉遍历列表的新调用方都会把它当成一条运行记录）。
        """
        with get_db() as session:
            total = session.scalar(
                select(func.count())
                .select_from(TaskRun)
                .where(TaskRun.task_key == task_key)
            )
            stmt = (
                select(TaskRun)
                .where(TaskRun.task_key == task_key)
                .order_by(TaskRun.id.desc())
                .limit(limit)
            )
            runs = [r.as_dict() for r in session.scalars(stmt)]
        return int(total or 0), runs


class ImportedFileDAO:
    @staticmethod
    def get_by_path(path: str) -> Optional[dict]:
        with get_db() as session:
            row = session.scalar(
                select(ImportedFile).where(ImportedFile.path_key == path_key_of(path))
            )
            return row.as_dict() if row is not None else None

    @staticmethod
    def content_hashes(path_keys: list[str]) -> dict[str, str]:
        """批量取既有内容指纹（path_key -> content_hash）

        目录扫描一轮只需一次往返，替代逐文件 get_by_path 的 N+1 查询；
        IN 列表按 900 分片，规避 SQLite 绑定变量上限。
        """
        found: dict[str, str] = {}
        if not path_keys:
            return found
        with get_db() as session:
            for i in range(0, len(path_keys), 900):
                chunk = path_keys[i : i + 900]
                rows = session.execute(
                    select(ImportedFile.path_key, ImportedFile.content_hash).where(
                        ImportedFile.path_key.in_(chunk)
                    )
                ).all()
                found.update(dict(rows))
        return found

    @staticmethod
    def upsert(
        path: str,
        *,
        file_name: str,
        user_id: str,
        size: int,
        mtime: float,
        content_hash: str,
        status: str,
        message: str = "",
        inserted: int = 0,
    ) -> None:
        """登记/更新文件处理结果（按 path_key 幂等，内容变化时覆盖旧记录）"""
        now = time.time()
        values = dict(
            path=path[:500],
            file_name=file_name[:255],
            user_id=user_id,
            size=size,
            mtime=mtime,
            content_hash=content_hash,
            status=status,
            message=message[:500],
            inserted=inserted,
            updated_at=now,
        )
        with get_db() as session:
            exists = session.scalar(
                select(ImportedFile.id).where(
                    ImportedFile.path_key == path_key_of(path)
                )
            )
            if exists is None:
                session.add(ImportedFile(path_key=path_key_of(path), **values))
            else:
                session.execute(
                    update(ImportedFile)
                    .where(ImportedFile.path_key == path_key_of(path))
                    .values(**values)
                )
