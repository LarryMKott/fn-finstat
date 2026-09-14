"""自动化底座数据访问层：定时任务定义/运行历史 + 已导入文件登记

任务调度状态机（详见 docs/devlog 开发计划 T-5.1）：
- 认领：try_claim 以原子 UPDATE 抢占 running_at 软锁，成功返回 TaskLock
  句柄；句柄携带认领时间戳作为持有者凭证，释放/成功/失败三条收尾路径
  只作用于自己那次认领的锁 —— 锁协议由数据库层强制，而非调用方约定；
  超过 LOCK_TIMEOUT 未释放的锁视为死锁，可被重新认领
- 成功：failure_count 清零，next_run_at = now + interval
- 失败：按 1/2/4 倍间隔退避，连续 MAX_FAILURES 次失败自动停用
"""

import hashlib
import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.base import get_db, is_unique_violation, translate_unique_violation
from app.db.models import ImportedFile, ScheduledTask, TaskRun

# 软锁超时（秒）：任务执行超过该时长仍未释放锁，视为死锁可被重新认领
LOCK_TIMEOUT = 30 * 60
# 连续失败自动停用的阈值；间隔退避倍数见 BACKOFF_MULTIPLIERS
MAX_FAILURES = 5
# 连续失败的间隔退避倍数（第 1/2 次失败后 1/2 倍间隔，其后封顶 4 倍）
BACKOFF_MULTIPLIERS = (1, 2, 4)
# 运行历史保留窗口：task_runs 只服务于「最近执行结果/失败原因」展示，超窗清理防无限增长
RUN_RETENTION_SECONDS = 30 * 24 * 60 * 60


def path_key_of(path: str) -> str:
    """文件路径的唯一键（sha256 摘要）：路径任意长且跨方言索引安全"""
    return hashlib.sha256(path.replace("\\", "/").encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TaskLock:
    """软锁句柄：try_claim 成功的凭证，封装一次认领的完整生命周期

    携带认领时写入 running_at 的时间戳作为持有者凭证 —— 释放/成功/失败
    三条收尾 UPDATE 都附加「running_at 仍等于本次认领值」条件：
    - 锁超时被他人重新认领后，先结束的旧执行者不会误清新持有者的锁，
      也不会把新持有者执行中的任务计一次失败
    - 停用、异常兜底等任何路径都不再无条件清锁，防止同任务并发双跑
    """

    task_key: str
    claimed_at: float

    def _owner_update(self, session, **values) -> int:
        """带持有者校验的收尾 UPDATE，返回影响行数（0 = 已不持有该锁）"""
        return session.execute(
            update(ScheduledTask)
            .where(
                ScheduledTask.task_key == self.task_key,
                ScheduledTask.running_at == self.claimed_at,
            )
            .values(**values)
        ).rowcount

    def release(self) -> None:
        """释放软锁（记录路径自身抛异常时的兜底口）；已不持有该锁时不动作"""
        with get_db() as session:
            self._owner_update(
                session, running_at=None, locked_by="", locked_until=None
            )

    def record_success(self, now: float, message: str = "") -> None:
        """执行成功：清零失败计数、按间隔重排下次执行、释放锁（单条 UPDATE）"""
        with get_db() as session:
            self._owner_update(
                session,
                failure_count=0,
                next_run_at=now + ScheduledTask.interval_minutes * 60,
                running_at=None,
                locked_by="",
                locked_until=None,
                last_status="ok",
                last_message=message[:500],
                updated_at=now,
            )

    def record_failure(self, now: float, message: str) -> bool:
        """执行失败：按 1/2/4 倍间隔退避；连续 MAX_FAILURES 次失败自动停用

        返回是否触发了自动停用（调用方据此产生通知事件）。

        并发安全：
        - failure_count 用「数据库端原子自增」而非先读后写，与 try_claim
          的原子 UPDATE 风格保持一致 —— 若并发触发（手动「立即执行」与调度
          tick 撞上），先读后写会丢失计数并使自动停用阈值失效
        - 持有者校验失败（锁已超时被他人重新认领 / 任务已删除）时返回
          False 且不写任何状态，避免污染新持有者的执行环境
        """
        with get_db() as session:
            rowcount = self._owner_update(
                session,
                failure_count=ScheduledTask.failure_count + 1,
                running_at=None,
                locked_by="",
                locked_until=None,
                updated_at=now,
            )
            if rowcount == 0:
                return False  # 已不持有该锁：不计失败，也不清他人锁
            # 同事务读回自增后的真实值（原子自增的结果只有数据库知道）
            task = session.scalar(
                select(ScheduledTask).where(ScheduledTask.task_key == self.task_key)
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
                multiplier = BACKOFF_MULTIPLIERS[min(failure_count - 1, 2)]
                values["next_run_at"] = now + task.interval_minutes * 60 * multiplier
            session.execute(
                update(ScheduledTask)
                .where(ScheduledTask.task_key == self.task_key)
                .values(**values)
            )
        return disable


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
        """启用时清零失败计数；停用仅摘除调度指针（next_run_at），不动软锁

        - 启用分支刻意不动 next_run_at / running_at：next_run_at 停用时已置
          None，启用后最近一个 tick 就会执行；running_at 若非空说明上一轮
          仍在执行，其收尾路径会按认领时间戳安全释放
        - 停用分支同样不清软锁：任务执行中被停用又立刻手动执行时
          （try_claim 对手动路径不要求 enabled），清锁会让下一次认领成功、
          同一任务并发跑两遍。锁的生命周期整体收口在 TaskLock 的收尾
          UPDATE，软锁字段只有持锁执行者（或 LOCK_TIMEOUT 超时重认领）可清。
        """
        values: dict = {
            "enabled": enabled,
            "failure_count": 0,
            "updated_at": time.time(),
        }
        if not enabled:
            values["next_run_at"] = None
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
    def try_claim(
        task_key: str, now: float, require_enabled: bool = True
    ) -> Optional[TaskLock]:
        """原子认领软锁：任务空闲（或锁已超时）时置 running_at，成功返回锁句柄

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
        return TaskLock(task_key, now) if rowcount > 0 else None

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
            # 保留窗口外的旧记录顺手清理：写入频率为分钟级，无需独立清理任务
            session.execute(
                delete(TaskRun).where(
                    TaskRun.started_at < time.time() - RUN_RETENTION_SECONDS
                )
            )
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
        """登记/更新文件处理结果（按 path_key 幂等，内容变化时覆盖旧记录）

        并发安全：先插入，唯一约束冲突（手动导入与目录扫描同时登记同一
        文件）时回退为覆盖更新 —— 插入失败即证明他方已登记，更新以本方
        结果为准。非唯一约束的 IntegrityError 原样上抛，不掩盖真实数据库故障。
        """
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
        path_key = path_key_of(path)
        try:
            with get_db() as session:
                session.add(ImportedFile(path_key=path_key, **values))
                session.flush()
        except IntegrityError as exc:
            if not is_unique_violation(exc):
                raise
            with get_db() as session:
                session.execute(
                    update(ImportedFile)
                    .where(ImportedFile.path_key == path_key)
                    .values(**values)
                )
