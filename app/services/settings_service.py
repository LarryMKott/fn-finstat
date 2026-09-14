"""设置业务：查看当前数据库、测试目标连接、迁移现有数据并切换、运行日志

「迁移并切换」流程（互斥锁防并发）：
    校验目标 ≠ 当前库 → 确保驱动可用 → 测试连接 → 目标库按当前模型建表 →
    搬移 bills/categories（目标非空时按唯一键去重合并）→ 持久化连接配置 →
    运行期切换引擎 → 更新数据库类型标记
源数据库只读不写，迁移失败或后悔可随时改回配置回退。
"""

import logging
from typing import Optional

from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.config import (
    APP_AUTHOR,
    APP_AUTHOR_URL,
    APP_NAME,
    APP_REPO_URL,
    APP_VERSION,
    DB_PATH,
    LOG_PATH,
    write_db_config_file,
)
from app.core.context import GatewayUser
from app.core.errors import ConfigError, ErrorCode, NotFoundError
from app.db.base import (
    LATEST_SCHEMA_VERSION,
    activate_engine,
    build_engine,
    current_engine,
    current_settings,
    read_schema_version,
    write_db_type_marker,
)
from app.db.copy import copy_database
from app.db.dao.bill_dao import BillDAO
from app.db.drivers import ensure_driver
from app.db.models import Base, Bill, Category
from app.schemas.settings import (
    AboutInfo,
    ConnectionTestResult,
    DatabaseInfo,
    MigrateResult,
    RuntimeLog,
    TargetDatabase,
    UserClaimResult,
)
from app.services.backup_service import RESTORE_LOCK

logger = logging.getLogger(__name__)

# 迁移与「备份恢复」复用同一把进程级互斥锁（backup_service.RESTORE_LOCK）：
# 两者都会重写或切换全局数据视图 —— 迁移在锁内 activate_engine 切换运行引擎，
# 若恢复并发进行，其 get_db() 可能仍取自切换前的旧引擎，写入落到被弃用的库，
# 表现为「恢复了但数据没变」。统一互斥后，迁移 / 恢复 / 导入三者两两排斥
# （import_service 以非阻塞方式获取同一把锁，迁移期间新导入同样被拒绝）。

# 日志尾部单次读取上限：日志单文件上限 10MB，取尾部 1MB 足够展示最近几百行
_LOG_TAIL_BYTES = 1024 * 1024

_APP_DESCRIPTION = (
    "个人收支统计应用：微信/支付宝/京东/云闪付账单导入、智能分类、"
    "预算管理、净资产追踪与收支可视化。"
)


def get_about_info(theme: str = "") -> AboutInfo:
    """应用「关于」信息（来自 config 常量，版本号与 manifest 同步维护）

    theme 为网关透传的宿主主题（light/dark），供前端在跨域 iframe 场景下
    兜底跟随飞牛的日间/夜间模式；未透传时为空串。
    """
    return AboutInfo(
        app_name=APP_NAME,
        version=APP_VERSION,
        author=APP_AUTHOR,
        author_url=APP_AUTHOR_URL,
        repo_url=APP_REPO_URL,
        description=_APP_DESCRIPTION,
        fnos_theme=theme,
    )


def _counts(session: Session, user_id: Optional[str] = None) -> tuple[int, int]:
    """流水/分类计数；user_id 为 None 时统计全部账号（判空目标库用），否则仅该账号"""
    conds = [Bill.user_id == user_id] if user_id is not None else []
    bills = session.scalar(select(func.count()).select_from(Bill).where(*conds)) or 0
    categories = session.scalar(select(func.count()).select_from(Category)) or 0
    return bills, categories


def _conn_message(exc: Exception) -> str:
    """提炼驱动异常的用户可读信息，去掉 SQLAlchemy 冗长包装"""
    orig = getattr(exc, "orig", None)
    message = str(orig or exc).strip()
    return message.split("\n")[0] or "连接失败"


def get_database_info(user: GatewayUser) -> DatabaseInfo:
    """当前数据库概览：类型/连接信息（密码不回传）、当前账号数据量与待认领历史流水数

    非管理员脱敏：服务器文件路径、外部库主机/端口/账号、待认领数量不回传
    （认领本身已收紧为管理员操作），普通用户只需要数据库类型与自己的数据量。
    """
    settings = current_settings()
    with Session(current_engine()) as session:
        bills, categories = _counts(session, user.user_id)
        version = read_schema_version(session) or 0
    # 与 require_admin 同口径：无网关身份（本地/独立部署）视为唯一用户放行
    is_admin = not user.user_id or user.is_admin
    info = DatabaseInfo(
        db_type=settings.db_type,
        name=settings.name,
        has_password=bool(settings.password),
        bills=bills,
        categories=categories,
        schema_version=version,
        schema_latest=LATEST_SCHEMA_VERSION,
        user_id=user.user_id,
        user_name=user.user_name or None,
        unassigned_bills=BillDAO.count_unassigned() if is_admin else 0,
    )
    if settings.db_type == "sqlite":
        info.name = DB_PATH.name
        if is_admin:
            info.sqlite_path = str(DB_PATH)
    elif is_admin:
        info.host = settings.host
        info.port = settings.port
        info.user = settings.user
    return info


def claim_legacy_bills(user: GatewayUser) -> UserClaimResult:
    """把升级前入库、无归属的历史流水认领到当前账号"""
    claimed = BillDAO.claim_unassigned(user.user_id)
    if claimed:
        logger.info(
            "账号 %s（%s）认领了 %s 条历史流水", user.user_id, user.user_name, claimed
        )
    return UserClaimResult(
        claimed=claimed,
        message=f"已认领 {claimed} 条历史流水" if claimed else "没有需要认领的历史流水",
    )


def _ensure_driver_ready(db_type: str) -> None:
    """驱动可用性检查：缺失时抛 ConfigError（含安装指引），不透出 RuntimeError"""
    try:
        ensure_driver(db_type)
    except RuntimeError as exc:
        raise ConfigError(str(exc)) from exc


def test_target_connection(target: TargetDatabase) -> ConnectionTestResult:
    """测试目标库连通性；连不上返回 ok=False（不抛异常），驱动缺失等环境问题抛 ConfigError"""
    settings = target.to_settings()
    _ensure_driver_ready(settings.db_type)
    engine = build_engine(settings)
    try:
        with engine.connect() as conn:
            server_version = conn.execute(text("SELECT version()")).scalar()
        target_empty: bool | None = None
        if inspect(engine).has_table("bills"):
            with Session(engine) as session:
                bills, categories = _counts(session)
                target_empty = bills == 0 and categories == 0
        return ConnectionTestResult(
            ok=True,
            message="连接成功",
            server_version=str(server_version or "").split("\n")[0],
            target_empty=target_empty,
        )
    except DBAPIError as exc:
        logger.warning(
            "测试目标数据库连接失败（%s %s:%s/%s）：%s",
            settings.db_type,
            settings.host,
            settings.port,
            settings.name,
            exc.orig,
        )
        return ConnectionTestResult(ok=False, message=_conn_message(exc))
    finally:
        engine.dispose()


def migrate_and_switch(target: TargetDatabase) -> MigrateResult:
    """迁移数据到目标库并立即切换（完整流程见模块 docstring），失败抛 ConfigError"""
    target_settings = target.to_settings()
    current = current_settings()
    if current.db_type == target_settings.db_type != "sqlite" and (
        current.host,
        current.port,
        current.name,
        current.user,
    ) == (
        target_settings.host,
        target_settings.port,
        target_settings.name,
        target_settings.user,
    ):
        raise ConfigError("目标数据库与当前使用的数据库相同，无需迁移")

    with RESTORE_LOCK:
        _ensure_driver_ready(target_settings.db_type)
        source_engine = current_engine()
        engine = build_engine(target_settings)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            Base.metadata.create_all(engine)
            stats = copy_database(source_engine, engine, LATEST_SCHEMA_VERSION)
        except DBAPIError as exc:
            engine.dispose()
            raise ConfigError(
                f"迁移失败：{_conn_message(exc)}", code=ErrorCode.DB_MIGRATE_FAILED
            ) from exc
        except Exception:
            engine.dispose()
            raise

        write_db_config_file(target_settings)
        activate_engine(target_settings, engine)
        write_db_type_marker(target_settings)

    logger.info(
        "数据迁移完成：%s 条流水、%s 个分类 → %s %s:%s/%s（源库保留不动）",
        stats["copied_bills"],
        stats["copied_categories"],
        target_settings.db_type,
        target_settings.host,
        target_settings.port,
        target_settings.name,
    )
    return MigrateResult(
        message="迁移完成，已切换到新数据库",
        source_bills=stats["source_bills"],
        source_categories=stats["source_categories"],
        copied_bills=stats["copied_bills"],
        copied_categories=stats["copied_categories"],
        merged=stats["target_had_data"],
        switched=True,
    )


def _read_log_tail(path, max_bytes: int = _LOG_TAIL_BYTES) -> tuple[str, bool]:
    """读取日志文件尾部内容，返回 (文本, 是否因超出读取范围被截断)

    open/seek/read 整体纳入异常兜底：stat 与读取之间日志可能恰好完成轮转
    （旧文件被改名、新文件 0 字节），对空文件负向 seek 会抛 OSError 使日志
    接口 500；读失败按空内容降级即可。
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(-min(size, max_bytes), 2)  # 只读尾部，避免大文件整读进内存
                data = f.read()
                newline = data.find(b"\n")  # 丢弃首行残段，保证按完整行展示
                if newline != -1:
                    data = data[newline + 1 :]
                return data.decode("utf-8", errors="replace"), True
            data = f.read()
        return data.decode("utf-8", errors="replace"), False
    except OSError:
        return "", False


def get_runtime_logs(lines: int = 300) -> RuntimeLog:
    """运行日志尾部：设置页展示最近 N 行（含导入/智能分类等过程日志）"""
    lines = max(10, min(lines, 2000))
    content, truncated = _read_log_tail(LOG_PATH)
    shown = content.splitlines()
    if len(shown) > lines:
        shown = shown[-lines:]
        truncated = True
    try:
        size = LOG_PATH.stat().st_size
    except OSError:
        size = 0
    return RuntimeLog(
        path=str(LOG_PATH),
        size=size,
        truncated=truncated,
        lines=len(shown),
        content="\n".join(shown),
    )


def read_runtime_log_bytes() -> tuple[str, bytes]:
    """完整运行日志内容（仅当前日志，不含轮转备份），返回 (文件名, 内容字节)

    返回原始数据而非 Response 对象：服务层不感知 Web 框架，响应头由路由层构建。
    """
    if not LOG_PATH.exists():
        raise NotFoundError("日志文件不存在", code=ErrorCode.LOG_NOT_FOUND)
    try:
        data = LOG_PATH.read_bytes()
    except OSError as exc:
        # 读取失败必须显式报错：伪装成空文件会让用户下载到 0 字节却显示成功
        raise ConfigError(f"日志文件读取失败：{exc}") from exc
    return LOG_PATH.name, data
