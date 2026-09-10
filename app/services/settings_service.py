"""设置业务：查看当前数据库、测试目标连接、迁移现有数据并切换

「迁移并切换」流程（互斥锁防并发）：
    校验目标 ≠ 当前库 → 确保驱动可用 → 测试连接 → 目标库按当前模型建表 →
    搬移 bills/categories（目标非空时按唯一键去重合并）→ 持久化连接配置 →
    运行期切换引擎 → 更新数据库类型标记
源数据库只读不写，迁移失败或后悔可随时改回配置回退。
"""
import logging
import threading
from typing import Optional

from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.deps import GatewayUser
from app.config import DB_PATH, write_db_config_file
from app.db.base import (
    LATEST_SCHEMA_VERSION, activate_engine, build_engine, current_engine,
    current_settings, write_db_type_marker, _get_schema_version,
)
from app.db.copy import copy_database
from app.db.dao.bill_dao import BillDAO
from app.db.drivers import ensure_driver
from app.db.models import Base, Bill, Category
from app.schemas.settings import (
    ConnectionTestResult, DatabaseInfo, MigrateResult, TargetDatabase, UserClaimResult,
)

logger = logging.getLogger(__name__)

_MIGRATE_LOCK = threading.Lock()


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
    settings = current_settings()
    with Session(current_engine()) as session:
        bills, categories = _counts(session, user.user_id)
        unassigned = session.scalar(
            select(func.count()).select_from(Bill).where(Bill.user_id == "")
        ) or 0
        version = _get_schema_version(session) or 0
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
        unassigned_bills=unassigned,
    )
    if settings.db_type == "sqlite":
        info.name = DB_PATH.name
        info.sqlite_path = str(DB_PATH)
    else:
        info.host = settings.host
        info.port = settings.port
        info.user = settings.user
    return info


def claim_legacy_bills(user: GatewayUser) -> UserClaimResult:
    """把升级前入库、无归属的历史流水认领到当前账号"""
    claimed = BillDAO.claim_unassigned(user.user_id)
    if claimed:
        logger.info("账号 %s（%s）认领了 %s 条历史流水", user.user_id, user.user_name, claimed)
    return UserClaimResult(
        claimed=claimed,
        message=f"已认领 {claimed} 条历史流水" if claimed else "没有需要认领的历史流水",
    )


def test_target_connection(target: TargetDatabase) -> ConnectionTestResult:
    """测试目标库连通性；连不上返回 ok=False（不抛异常），驱动缺失/安装失败抛 RuntimeError"""
    settings = target.to_settings()
    ensure_driver(settings.db_type)
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
        logger.warning("测试目标数据库连接失败（%s %s:%s/%s）：%s",
                       settings.db_type, settings.host, settings.port, settings.name, exc.orig)
        return ConnectionTestResult(ok=False, message=_conn_message(exc))
    finally:
        engine.dispose()


def migrate_and_switch(target: TargetDatabase) -> MigrateResult:
    target_settings = target.to_settings()
    current = current_settings()
    if (current.db_type == target_settings.db_type != "sqlite"
            and (current.host, current.port, current.name, current.user)
            == (target_settings.host, target_settings.port, target_settings.name, target_settings.user)):
        raise RuntimeError("目标数据库与当前使用的数据库相同，无需迁移")

    with _MIGRATE_LOCK:
        ensure_driver(target_settings.db_type)
        source_engine = current_engine()
        engine = build_engine(target_settings)
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            Base.metadata.create_all(engine)
            stats = copy_database(source_engine, engine, LATEST_SCHEMA_VERSION)
        except DBAPIError as exc:
            engine.dispose()
            raise RuntimeError(f"迁移失败：{_conn_message(exc)}") from exc
        except Exception:
            engine.dispose()
            raise

        write_db_config_file(target_settings)
        activate_engine(target_settings, engine)
        write_db_type_marker(target_settings)

    logger.info(
        "数据迁移完成：%s 条流水、%s 个分类 → %s %s:%s/%s（源库保留不动）",
        stats["copied_bills"], stats["copied_categories"],
        target_settings.db_type, target_settings.host, target_settings.port, target_settings.name,
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
