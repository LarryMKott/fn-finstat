"""调度底座与目录监听场景自检（T-5.1 / T-5.3）

模拟「断电重启错过补跑」「并发互斥」「失败退避与自动停用」「目录监听
指纹判重」四个关键场景，全部基于临时 SQLite 库，不触碰真实数据。

用法：python scripts/verify_scheduler.py   （输出通过率，非 0 退出码表示失败）
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine  # noqa: E402

from app.config import DEFAULT_CATEGORIES, DBSettings  # noqa: E402
from app.db.base import _STATE, insert_ignore_rows, set_schema_version  # noqa: E402
from app.db.dao import task_dao  # noqa: E402
from app.db.models import Base, Category  # noqa: E402
from app.services import import_watch_service, nas_service, scheduler  # noqa: E402

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def make_db(tmp: Path):
    engine = create_engine(f"sqlite:///{(tmp / 'verify.db').as_posix()}")
    Base.metadata.create_all(engine)
    old = _STATE.activate(DBSettings(db_type="sqlite"), engine)
    if old is not None:
        old.dispose()
    with engine.begin() as conn:
        insert_ignore_rows(
            conn, Category.__table__, [{"name": n} for n in DEFAULT_CATEGORIES]
        )
    from sqlalchemy.orm import Session

    with Session(engine) as session:
        set_schema_version(session, 4)
        session.commit()
    return engine


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="verify-scheduler-") as td:
        tmp = Path(td)
        engine = make_db(tmp)
        state = {"calls": 0, "fail": False}

        def flaky_task():
            state["calls"] += 1
            if state["fail"]:
                raise RuntimeError("模拟执行失败")
            return 1, "ok"

        scheduler.register_task("verify_task", "自检任务", 10, flaky_task)
        task_dao.TaskDAO.ensure_task("verify_task", "自检任务", 10)
        scheduler.register_task(
            import_watch_service.TASK_KEY,
            "NAS 目录监听导入",
            30,
            import_watch_service.scan_and_import,
        )
        task_dao.TaskDAO.ensure_task(
            import_watch_service.TASK_KEY, "NAS 目录监听导入", 30
        )

        print("== 场景 1：断电重启后错过补跑合并为一次 ==")
        overdue = time.time() - 10 * 60 * 8  # 按间隔可错过约 8 个周期
        summaries = scheduler.run_due_tasks(overdue)
        ran = [s for s in summaries if s["task_key"] == "verify_task"]
        check("过期任务补跑执行", len(ran) == 1 and ran[0]["ok"])
        row = task_dao.TaskDAO.get("verify_task")
        check(
            "补跑后按间隔重排（合并不逐次）", row["next_run_at"] > overdue + 10 * 60 - 1
        )

        print("== 场景 2：并发互斥 ==")
        now = time.time()
        assert task_dao.TaskDAO.try_claim("verify_task", now)
        try:
            scheduler.run_task_now("verify_task", now + 1)
            check("持锁期间手动触发被拒绝", False, "未抛出冲突异常")
        except Exception as exc:
            check("持锁期间手动触发被拒绝", "正在运行" in str(exc), str(exc))
        from app.db.dao.task_dao import LOCK_TIMEOUT

        # 锁定时间已超出 LOCK_TIMEOUT，可重新认领
        relocked = task_dao.TaskDAO.try_claim("verify_task", now + LOCK_TIMEOUT + 5)
        assert relocked is not None
        check("超时软锁可重新认领", True)
        # 停用不再清软锁（锁只能由持有者按认领时间戳释放），场景 3 前显式还锁
        relocked.release()

        print("== 场景 3：失败退避与连续失败自动停用 ==")
        state["fail"] = True
        # 重新置为待执行（set_enabled(False→True) 会清空 next_run_at 与失败计数）
        task_dao.TaskDAO.set_enabled("verify_task", False)
        task_dao.TaskDAO.set_enabled("verify_task", True)
        fake = time.time()
        disabled_at = None
        for i in range(task_dao.MAX_FAILURES):
            scheduler.run_due_tasks(fake)
            row = task_dao.TaskDAO.get("verify_task")
            if not row["enabled"]:
                disabled_at = i + 1
                break
            fake = row["next_run_at"] + 1  # 按退避后的下次执行时间推进
        check(
            f"连续 {disabled_at} 次失败后自动停用", disabled_at == task_dao.MAX_FAILURES
        )

        print("== 场景 4：目录监听指纹判重 ==")
        bills = tmp / "bills"
        bills.mkdir()
        nas_service.update_config(
            type("P", (), {"import_dir": str(bills)}), owner_user_id=""
        )
        csv_path = bills / "alipay.csv"
        csv_path.write_text(
            "支付宝交易记录明细查询\n"
            "交易时间,交易分类,交易对方,对方账号,商品说明,收/支,金额,收/付款方式,交易状态,交易订单号,商家订单号,备注\n"
            "2024-01-01 08:30:00,餐饮美食,肯德基,,肯德基宅急送,支出,45.00,余额,交易成功,VER-0001,,, \n",
            encoding="utf-8",
        )
        affected, message = import_watch_service.scan_and_import()
        check("新文件首个周期入库", affected == 1 and "新增文件 1" in message, message)
        affected, message = import_watch_service.scan_and_import()
        check("同内容不重复导入", affected == 0 and "未变化 1" in message, message)
        content = (
            csv_path.read_text(encoding="utf-8")
            .replace("45.00", "66.00")
            .replace("VER-0001", "VER-0002")
        )
        csv_path.write_text(content, encoding="utf-8")
        affected, message = import_watch_service.scan_and_import()
        check(
            "同名变更文件识别为变更并导入",
            affected == 1 and "变更文件 1" in message,
            message,
        )

        engine.dispose()  # Windows 下先释放连接，临时目录才能删除

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n自检完成：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
