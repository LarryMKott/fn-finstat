"""测试夹具：每个测试用例使用独立的临时 SQLite 库，通过 _STATE 激活引擎

DAO/服务层统一经 app.db.base.get_db() 获取会话，因此测试只需替换 _STATE
中的引擎即可让整条数据链路指向临时库，无需 mock 任何 DAO。
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.config import DEFAULT_CATEGORIES, DBSettings
from app.db.base import (
    LATEST_SCHEMA_VERSION,
    insert_ignore_rows,
    set_schema_version,
)
from app.db.engine import _STATE
from app.db.ledgers import ensure_default_ledger
from app.db.models import Base, Category
from app.api import ai as ai_api
from app.services import ai_service, notify_service
from app.utils import net_guard

USER_A = "10001"
USER_B = "10002"


def make_engine(db_file: Path):
    return create_engine(
        f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False}
    )


@pytest.fixture()
def db(tmp_path: Path):
    """独立临时库：建表 + 记录 schema 版本 + 预置默认分类，测试结束释放连接"""
    engine = make_engine(tmp_path / "test.db")
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
        set_schema_version(session, LATEST_SCHEMA_VERSION)
        # 与 init_db 保持一致：默认账本必须存在（ledger_id 列的默认值指向它）
        ensure_default_ledger(session)
        session.commit()
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def ai_config_isolated(tmp_path: Path, monkeypatch):
    """本地文件状态隔离：AI/NAS 配置与运行日志路径都指向临时文件，单测不读写本地真实文件；

    同时清除 DEEPSEEK_API_KEY 环境变量（.env.dev 可能注入），保证默认
    未配置密钥、AI 归类整体跳过，避免单测触发真实外部请求。
    """
    import app.config as config
    import app.file_settings as file_settings

    monkeypatch.setattr(file_settings, "AI_CONFIG_FILE", tmp_path / "ai_config.json")
    monkeypatch.setattr(file_settings, "NAS_CONFIG_FILE", tmp_path / "nas_config.json")
    monkeypatch.setattr(
        file_settings, "NOTIFY_CONFIG_FILE", tmp_path / "notify_config.json"
    )
    monkeypatch.setattr(config, "LOG_PATH", tmp_path / "app.log")
    # settings_service 以 from-import 引用 LOG_PATH，需同步替换其入口
    monkeypatch.setattr("app.services.settings_service.LOG_PATH", tmp_path / "app.log")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    # 调度循环只在生产 lifespan 启动；测试环境显式关闭兜底
    monkeypatch.setenv("SCHEDULER_ENABLED", "0")


@pytest.fixture()
def client(db):
    """路由级 API 客户端：仅挂载业务路由，不触发 main.app 的 lifespan/init_db

    与 main.app 注册同一套全局异常处理器，保证测试看到与生产一致的错误响应结构。
    """
    from app.api import (
        ai,
        asset,
        audit,
        automation,
        bill,
        budget,
        category,
        coach,
        family,
        forecast,
        ledger,
        loans,
        mcp,
        nas,
        nl_query,
        notify,
        reimb,
        remote_backup,
        savings,
        settings,
        stat,
        tokens,
        update,
        upload,
    )
    from app.core.handlers import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)
    for router in (
        upload.router,
        nas.router,
        bill.router,
        budget.router,
        asset.router,
        audit.router,
        reimb.router,
        remote_backup.router,
        savings.router,
        category.router,
        coach.router,
        ledger.router,
        loans.router,
        mcp.router,
        family.router,
        stat.router,
        tokens.router,
        forecast.router,
        settings.router,
        update.router,
        automation.router,
        notify.router,
        notify.config_router,
        ai.router,
        nl_query.router,
    ):
        app.include_router(router)
    return TestClient(app)


def make_bill_records(count: int, prefix: str = "TX", **overrides) -> list[dict]:
    """批量构造标准化流水字典（与解析器输出同构）

    未显式给 tx_id 时按 prefix+序号生成；同一测试里给不同账号造数据时
    必须用不同 prefix（tx_id 全局唯一，跨账号也会去重）。
    """
    base = {
        "tx_time": "2024-01-01 12:00:00",
        "account": "wechat",
        "tx_type": "expense",
        "merchant": "测试商户",
        "amount": 10.0,
        "category": "其他",
        "tx_id": None,
        "remark": "",
    }
    records = []
    for i in range(count):
        record = {**base, **overrides}
        if "tx_id" not in overrides:
            record["tx_id"] = f"{prefix}-{i:04d}"
        records.append(record)
    return records


def assert_report(data, **expected):
    """断言上传导入报告（UploadReport）字段：未给出的字段按默认值校验，未知字段报错

    收敛各测试逐字复制的 7 字段字典字面量：报告新增字段时只需改这里。
    """
    defaults = {
        "total": 0,
        "inserted": 0,
        "skipped": 0,
        "ai_classified": 0,
        "skipped_dup": 0,
        "failed": 0,
        "unrecognized": 0,
        "details": [],
    }
    assert set(data) == set(
        defaults
    ), f"报告出现未知/缺失字段: {set(data) ^ set(defaults)}"
    for key, value in {**defaults, **expected}.items():
        assert data[key] == value, f"报告字段 {key}: 期望 {value!r}, 实际 {data[key]!r}"


@pytest.fixture()
def outbound_guard_bypass(monkeypatch):
    """让出站地址安全校验原样放行（SSRF 防线 M5-2/3 的测试替身）

    `app.utils.net_guard.validate_outbound_url` 在
    `notify_service.send_webhook` / `ai_service._chat` / `api/ai.py` 出站或保存
    前拦截内网/回环/云元数据地址，且解析不出 IP 时 fail-closed 拒绝。

    服务层单测有两个固有诉求与它冲突：

    1. **关注点隔离**：`test_ai_service` / `test_notify` 测的是「请求体构造」
       与「错误映射」，故意用 `https://api.example.com` 这类**不存在的假域名**
       （避免依赖真实网络）→ 新校验解析不出主机即拒绝，用例全红，而被测
       行为并未改变。
    2. **本地服务器**：`test_no_redirect` 必须起 `127.0.0.1` 临时服务器才能
       真实观察到 302 是否被跟随 → 回环正是防线要拦的目标。

    **使用边界（重要）**：
    - 显式 opt-in（非 autouse），调用方须写出参数名，读代码即可看出
      「这条用例不验证地址安全」。
    - 只在测试进程内替换，不改产品代码、不引入产品侧开关。
    - SSRF 防线的有效性由 `tests/security/` 覆盖（那里跑真实实现）。
    """

    def _allow(url, *, allow_private=False):
        return url

    monkeypatch.setattr(net_guard, "validate_outbound_url", _allow)
    # 三处调用点均为 `from ... import`，值已绑定到各自模块命名空间，需分别替换
    for module in (notify_service, ai_service, ai_api):
        monkeypatch.setattr(module, "validate_outbound_url", _allow, raising=False)
    return _allow
