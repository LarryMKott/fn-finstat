"""定时任务底座测试（T-5.1）：注册、补跑合并、并发互斥、软锁持有者凭证、失败退避、自动停用、运行历史"""

import time

import pytest
from app.db.dao import task_dao
from app.services import scheduler

KEY = "unit_task"


@pytest.fixture(autouse=True)
def _restore_registry():
    """快照/还原调度注册表：测试注册或改写的 fn 不泄漏到其他测试"""
    snapshot = {k: dict(v) for k, v in scheduler._REGISTRY.items()}
    yield
    scheduler._REGISTRY.clear()
    scheduler._REGISTRY.update(snapshot)


@pytest.fixture()
def task(db):
    """注册并落库一个测试任务：默认间隔 10 分钟，初始为已到期（next_run_at 为空）"""
    scheduler.register_task(KEY, "单元测试任务", 10, lambda: (1, "ok"))
    task_dao.TaskDAO.ensure_task(KEY, "单元测试任务", 10)
    return KEY


def test_ensure_task_idempotent(db):
    scheduler.register_task(KEY, "单元测试任务", 10, lambda: (1, "ok"))
    task_dao.TaskDAO.ensure_task(KEY, "单元测试任务", 10)
    # 二次注册不覆盖（用户可能已改过间隔/开关）
    task_dao.TaskDAO.set_interval(KEY, 99)
    task_dao.TaskDAO.ensure_task(KEY, "单元测试任务", 10)
    assert task_dao.TaskDAO.get(KEY)["interval_minutes"] == 99


def test_run_due_once_per_scan(task):
    """同一次扫描内任务最多执行一次；执行后按间隔重排到未来"""
    now = time.time()
    summaries = scheduler.run_due_tasks(now)
    assert len(summaries) == 1
    assert summaries[0]["ok"] is True
    assert summaries[0]["affected"] == 1
    row = task_dao.TaskDAO.get(KEY)
    assert row["next_run_at"] > now  # 已按间隔重排，不因"过期很久"而连续补跑
    assert row["failure_count"] == 0
    assert row["last_status"] == "ok"


def test_not_due_task_skipped(task):
    """未到期的任务不执行"""
    now = time.time()
    task_dao.TaskDAO.set_interval(KEY, 10)  # set_interval 会把 next_run_at 排到未来
    assert scheduler.run_due_tasks(now) == []


def test_concurrent_mutex(task):
    """并发互斥：任务持有未超时软锁时，第二次触发直接跳过并记录"""
    now = time.time()
    lock = task_dao.TaskDAO.try_claim(KEY, now)
    assert lock is not None
    assert task_dao.TaskDAO.try_claim(KEY, now) is None  # 未释放不可再抢
    summaries = scheduler.run_due_tasks(now + 1)
    assert summaries[0]["message"] == "正在运行中，跳过"
    lock.record_success(now + 1)  # 释放
    assert task_dao.TaskDAO.try_claim(KEY, now + 2) is not None


def test_stale_lock_reclaimable(task):
    """死锁防护：超过 LOCK_TIMEOUT 的软锁可被重新认领，且旧句柄不误清新锁"""
    now = time.time()
    from app.db.dao.task_dao import LOCK_TIMEOUT

    old = task_dao.TaskDAO.try_claim(KEY, now - LOCK_TIMEOUT - 10)
    assert old is not None
    # now 时该锁已超时，可再次认领
    new = task_dao.TaskDAO.try_claim(KEY, now)
    assert new is not None
    assert task_dao.TaskDAO.get(KEY)["running_at"] == pytest.approx(now)
    # 持有者凭证（评审 H-2 回归防护）：超时前的旧执行者收尾时，
    # 不得清掉新持有者刚认领的锁
    old.release()
    assert task_dao.TaskDAO.get(KEY)["running_at"] == pytest.approx(now)
    new.release()
    assert task_dao.TaskDAO.get(KEY)["running_at"] is None


def test_disable_running_task_keeps_lock(task):
    """停用执行中的任务不清软锁（评审 H-2）：防止停用后手动执行造成并发双跑"""
    now = time.time()
    lock = task_dao.TaskDAO.try_claim(KEY, now)
    assert lock is not None
    task_dao.TaskDAO.set_enabled(KEY, False)
    row = task_dao.TaskDAO.get(KEY)
    assert row["enabled"] is False
    assert row["next_run_at"] is None  # 调度指针被摘除
    assert row["running_at"] == pytest.approx(now)  # 软锁仍被持有
    # 锁被持有期间手动执行被拒（即使任务已停用）
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        scheduler.run_task_now(KEY)
    # 执行收尾按认领时间戳安全释放
    lock.record_success(now + 1)
    assert task_dao.TaskDAO.get(KEY)["running_at"] is None


def test_failure_backoff_and_auto_disable(task):
    """失败退避：1/2/4 倍间隔；连续 5 次失败自动停用"""

    def failing():
        raise RuntimeError("boom")

    scheduler._REGISTRY[KEY]["fn"] = failing
    now = time.time()
    intervals = []
    for i in range(task_dao.MAX_FAILURES):
        summaries = scheduler.run_due_tasks(now)
        assert summaries[0]["ok"] is False
        row = task_dao.TaskDAO.get(KEY)
        if i < task_dao.MAX_FAILURES - 1:
            intervals.append(row["next_run_at"] - now)
            now = row["next_run_at"] + 1
    row = task_dao.TaskDAO.get(KEY)
    assert row["enabled"] is False  # 连续 5 次失败自动停用
    assert row["next_run_at"] is None
    assert row["last_status"] == "disabled"
    # 退避倍数 1/2/4 × 10 分钟（第 4 次起封顶 4 倍）
    assert [round(i / 60, 1) for i in intervals] == [10.0, 20.0, 40.0, 40.0]
    # 停用后不再调度
    assert scheduler.run_due_tasks(now) == []
    # 重新启用后恢复
    task_dao.TaskDAO.set_enabled(KEY, True)
    assert task_dao.TaskDAO.get(KEY)["failure_count"] == 0


def test_record_failure_atomic_increment(task):
    """失败计数用数据库端原子自增：连续调用严格累加，不丢失计数

    回归防护（评审报告 M-1）：原实现「先 SELECT 读 failure_count，Python 加一，
    再 UPDATE 写回」在并发下会丢失更新，使自动停用阈值失效；现改为原子自增。
    """
    now = time.time()
    for expect in range(1, 4):
        lock = task_dao.TaskDAO.try_claim(KEY, now)
        assert lock is not None
        assert lock.record_failure(now, "boom") is False
        assert task_dao.TaskDAO.get(KEY)["failure_count"] == expect
    # 第 4 次未达阈值，第 5 次触发自动停用（MAX_FAILURES = 5）
    lock = task_dao.TaskDAO.try_claim(KEY, now)
    assert lock is not None
    assert lock.record_failure(now, "boom") is False
    lock = task_dao.TaskDAO.try_claim(KEY, now)
    assert lock is not None
    assert lock.record_failure(now, "boom") is True
    row = task_dao.TaskDAO.get(KEY)
    assert row["failure_count"] == task_dao.MAX_FAILURES
    assert row["enabled"] is False and row["next_run_at"] is None
    # 锁在自增同一事务内已释放
    assert row["running_at"] is None


def test_record_failure_lost_lock_is_noop(task):
    """锁超时被他人重新认领后，旧执行者的失败记录不生效（评审 H-2 回归防护）"""
    from app.db.dao.task_dao import LOCK_TIMEOUT

    now = time.time()
    old = task_dao.TaskDAO.try_claim(KEY, now - LOCK_TIMEOUT - 10)
    assert old is not None
    new = task_dao.TaskDAO.try_claim(KEY, now)
    assert new is not None
    assert old.record_failure(now + 1, "旧执行超时") is False
    row = task_dao.TaskDAO.get(KEY)
    assert row["failure_count"] == 0  # 不把新持有者执行中的任务计一次失败
    assert row["running_at"] == pytest.approx(now)  # 新持有者的锁未被误清
    assert row["last_status"] == ""


def test_record_failure_missing_task(task):
    """对不存在的任务记录失败：返回 False 且不抛异常"""
    lock = task_dao.TaskLock("no_such_task", time.time())
    assert lock.record_failure(time.time(), "x") is False


def test_redact_paths():
    """错误摘要脱敏：绝对路径只保留文件名，防止向非管理员泄漏服务器目录结构"""
    from app.services.scheduler import redact_paths

    assert (
        redact_paths(
            "OSError: [Errno 2] No such file or directory: '/volume1/homes/x/账单.csv'"
        )
        == "OSError: [Errno 2] No such file or directory: '账单.csv'"
    )
    assert (
        redact_paths("FileNotFoundError: D:\\nas\\bills\\微信账单.csv")
        == "FileNotFoundError: 微信账单.csv"
    )
    # 非路径文本与相对路径不受影响
    assert redact_paths("新增 3 条 / 跳过 1 条") == "新增 3 条 / 跳过 1 条"
    assert redact_paths("a/b 相对路径") == "a/b 相对路径"


def test_run_history_recorded(task):
    """运行历史：task_runs 记录起止/结果/影响条数，倒序可查；总数单独返回"""
    scheduler.run_due_tasks()
    scheduler._REGISTRY[KEY]["fn"] = lambda: (_ for _ in ()).throw(ValueError("bad"))
    scheduler.run_due_tasks(time.time() + 10 * 60 + 1)
    total, runs = task_dao.TaskDAO.recent_runs(KEY, 10)
    assert total == 2
    assert len(runs) == 2  # 列表内只有运行记录，不含元信息（评审 M-2）
    bad_run, ok_run = runs  # 倒序：最新的失败在前
    assert ok_run["ok"] is True and ok_run["affected"] == 1
    assert bad_run["ok"] is False and "bad" in bad_run["error"]


def test_run_task_now(task):
    """手动立即执行：成功返回摘要；不存在的任务报错"""
    result = scheduler.run_task_now(KEY)
    assert result["ok"] is True and result["affected"] == 1
    with pytest.raises(Exception):
        scheduler.run_task_now("no_such_task")
