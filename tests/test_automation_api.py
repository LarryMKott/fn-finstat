"""自动化接口测试（T-5.6 数据接口）：任务列表、立即执行、开关、间隔、运行历史、管理员守卫"""

import pytest

from app.db.dao import task_dao
from app.services import scheduler

from tests.conftest import USER_A, USER_B
from tests.test_api import A_HEADERS  # noqa: F401

KEY = "nas_watch"


@pytest.fixture(autouse=True)
def _restore_registry():
    """快照/还原调度注册表：KEY 与生产任务同名，覆盖的 fn 不泄漏到其他测试"""
    snapshot = {k: dict(v) for k, v in scheduler._REGISTRY.items()}
    yield
    scheduler._REGISTRY.clear()
    scheduler._REGISTRY.update(snapshot)


@pytest.fixture()
def task(db):
    scheduler.register_task(KEY, "NAS 目录监听导入", 30, lambda: (3, "新增 3 条"))
    task_dao.TaskDAO.ensure_task(KEY, "NAS 目录监听导入", 30)
    return KEY


def test_list_tasks_requires_nothing_special(client, task):
    resp = client.get("/api/settings/automation", headers={"X-Trim-Userid": USER_A})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["tasks"][0]["task_key"] == KEY
    assert data["tasks"][0]["enabled"] is True


def test_write_operations_reject_non_admin(client, task):
    """非管理员不可修改任务（require_admin）"""
    b = {"X-Trim-Userid": USER_B}
    assert (
        client.post(f"/api/settings/automation/{KEY}/run", headers=b).status_code == 403
    )
    assert (
        client.post(
            f"/api/settings/automation/{KEY}/toggle", json={"enabled": False}, headers=b
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/api/settings/automation/{KEY}", json={"interval_minutes": 10}, headers=b
        ).status_code
        == 403
    )


def test_run_now_and_history(client, task):
    """手动执行改为后台异步触发（nas_watch 单轮可能扫描上千文件，同步会挂住
    请求到网关超时）：接口立即返回触发回执，结果在运行历史中查看"""
    import time as _time

    resp = client.post(f"/api/settings/automation/{KEY}/run", headers=A_HEADERS)
    assert resp.status_code == 200
    result = resp.json()["data"]
    assert result["ok"] is True
    assert "已触发" in result["message"]

    # 后台线程执行完成后再查历史（轮询等待线程调度）
    history = None
    deadline = _time.time() + 5
    while _time.time() < deadline:
        resp = client.get(f"/api/settings/automation/{KEY}/runs", headers=A_HEADERS)
        history = resp.json()["data"]
        if history["total"] >= 1:
            break
        _time.sleep(0.05)
    assert history is not None and history["total"] == 1
    assert history["runs"][0]["affected"] == 3


def test_toggle_and_interval(client, task):
    resp = client.post(
        f"/api/settings/automation/{KEY}/toggle",
        json={"enabled": False},
        headers=A_HEADERS,
    )
    assert resp.json()["data"]["enabled"] is False
    assert task_dao.TaskDAO.get(KEY)["next_run_at"] is None

    resp = client.put(
        f"/api/settings/automation/{KEY}",
        json={"interval_minutes": 15},
        headers=A_HEADERS,
    )
    assert resp.json()["data"]["interval_minutes"] == 15
    # 非法间隔被 422 校验拦截
    assert (
        client.put(
            f"/api/settings/automation/{KEY}",
            json={"interval_minutes": 0},
            headers=A_HEADERS,
        ).status_code
        == 422
    )


def test_unknown_task_404(client, task):
    resp = client.post("/api/settings/automation/nope/run", headers=A_HEADERS)
    assert resp.status_code == 404
