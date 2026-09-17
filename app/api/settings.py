"""应用设置接口（备份导出/恢复与配置写操作仅管理员）"""

from datetime import datetime
from json import dumps

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile
from fastapi.responses import Response

from app.api.deps import AdminUser, CurrentUser, request_db_session
from app.config import MAX_UPLOAD_SIZE, MAX_UPLOAD_SIZE_MB
from app.core.errors import UploadTooLargeError
from app.schemas.common import ApiResponse, ok
from app.schemas.learned_rule import LearnedRuleOut, LearnedRuleUpdate
from app.schemas.settings import (
    AboutInfo,
    BackupRestoreResult,
    ConnectionTestResult,
    DatabaseInfo,
    MigrateResult,
    RuntimeLog,
    TargetDatabase,
    UserClaimResult,
)
from app.services import backup_service, learned_rule_service, settings_service
from app.utils.file_utils import content_disposition

router = APIRouter(
    prefix="/api/settings",
    tags=["应用设置"],
    dependencies=[Depends(request_db_session)],
)


@router.get(
    "/about",
    response_model=ApiResponse[AboutInfo],
    summary="应用关于信息（名称/版本/作者/仓库/宿主主题）",
)
def get_about(user: CurrentUser):
    """宿主主题经网关头透传，供前端跨域 iframe 场景兜底跟随飞牛日间/夜间模式"""
    return ok(settings_service.get_about_info(user.theme_raw))


@router.get(
    "/database",
    response_model=ApiResponse[DatabaseInfo],
    summary="当前数据库信息（含当前账号）",
)
def get_database_info(user: CurrentUser):
    return ok(settings_service.get_database_info(user))


@router.post(
    "/user/claim",
    response_model=ApiResponse[UserClaimResult],
    summary="认领历史数据（归入当前账号，仅管理员）",
)
def claim_legacy_bills(user: AdminUser):
    """把升级前入库、无归属的历史流水认领到当前飞牛账号（本地模式无网关身份时无需认领）

    仅管理员：认领会把全部无归属流水归到操作者账号名下，多用户场景这是
    影响他人数据的全局操作，不能由任意登录用户触发。
    """
    return ok(settings_service.claim_legacy_bills(user))


@router.post(
    "/database/test",
    response_model=ApiResponse[ConnectionTestResult],
    summary="测试目标数据库连接",
)
def test_target_database(_: AdminUser, target: TargetDatabase):
    return ok(settings_service.test_target_connection(target))


@router.post(
    "/database/migrate",
    response_model=ApiResponse[MigrateResult],
    summary="把现有数据迁移到新数据库并切换",
)
def migrate_database(_: AdminUser, target: TargetDatabase):
    """搬移现有流水/分类到目标库并立即切换（源数据库保留不动，可回退）"""
    return ok(settings_service.migrate_and_switch(target))


@router.get(
    "/logs",
    response_model=ApiResponse[RuntimeLog],
    summary="运行日志尾部（含导入/智能分类过程日志，仅管理员）",
)
def get_runtime_logs(
    _: AdminUser, lines: int = Query(300, ge=10, le=2000, description="返回末尾行数")
):
    """日志含全部账号的导入活动与服务器路径，与备份导出同为管理员数据"""
    return ok(settings_service.get_runtime_logs(lines))


@router.get(
    "/logs/download",
    summary="下载完整运行日志文件（仅管理员）",
    response_class=Response,
)
def download_runtime_log(_: AdminUser):
    log_name, data = settings_service.read_runtime_log_bytes()
    return Response(
        content=data,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": content_disposition(f"fn-finstat-{log_name}")},
    )


@router.get(
    "/backup",
    summary="下载全量数据备份（JSON，含全部账号，仅管理员）",
    response_class=Response,
)
def download_backup(_: AdminUser):
    data = backup_service.export_backup()
    content = dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"fn-finstat-backup-{stamp}.json"
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.post(
    "/restore",
    response_model=ApiResponse[BackupRestoreResult],
    summary="从备份 JSON 恢复数据（默认合并，replace=true 覆盖，仅管理员）",
)
def restore_backup(
    _: AdminUser,
    file: UploadFile = File(..., description="备份 JSON 文件"),
    replace: bool = Form(False, description="true=清空后导入（不可恢复）"),
):
    """合并模式按唯一键去重导入；覆盖模式先清空业务表再导入"""
    raw = file.file.read(MAX_UPLOAD_SIZE + 1)
    if len(raw) > MAX_UPLOAD_SIZE:
        raise UploadTooLargeError(f"备份文件超过 {MAX_UPLOAD_SIZE_MB}MB 大小限制")
    data = backup_service.load_backup_text(raw)
    result = backup_service.restore_backup(data, replace=replace)
    return ok(BackupRestoreResult(**result))


# ---- 分类学习规则（T-6.3）：全局共享影响所有账号的导入归类，写操作仅管理员 ----


@router.get(
    "/learned-rules",
    response_model=ApiResponse[list[LearnedRuleOut]],
    summary="分类学习规则列表",
)
def list_learned_rules(user: CurrentUser):
    """按 hits 降序返回全部规则；active = enabled 且证据达标（参与导入归类）"""
    return ok(learned_rule_service.list_rules())


@router.put(
    "/learned-rules/{rule_id}",
    response_model=ApiResponse[LearnedRuleOut],
    summary="编辑学习规则（改目标分类/启停，仅管理员）",
)
def update_learned_rule(_: AdminUser, rule_id: int, payload: LearnedRuleUpdate):
    """规则全局生效，编辑属管理面操作；category/enabled 缺省表示保持不变"""
    return ok(
        learned_rule_service.update_rule(rule_id, payload.category, payload.enabled)
    )


@router.delete(
    "/learned-rules/{rule_id}",
    response_model=ApiResponse[dict],
    summary="删除学习规则（仅管理员）",
)
def delete_learned_rule(_: AdminUser, rule_id: int = Path(..., description="规则 id")):
    """删除后该 (商户关键词 → 分类) 证据清零，可由后续纠正重新积累"""
    return ok({"ok": learned_rule_service.delete_rule(rule_id)})
