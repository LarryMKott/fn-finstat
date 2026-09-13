"""应用设置接口"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app.api.deps import GatewayUser, get_gateway_user, require_admin
from app.config import MAX_UPLOAD_SIZE
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
from app.services import backup_service, settings_service

router = APIRouter(prefix="/api/settings", tags=["应用设置"])


@router.get(
    "/about", response_model=AboutInfo, summary="应用关于信息（名称/版本/作者/仓库/宿主主题）"
)
def get_about(user: GatewayUser = Depends(get_gateway_user)):
    """宿主主题经网关头透传，供前端跨域 iframe 场景兜底跟随飞牛日间/夜间模式"""
    return settings_service.get_about_info(user.theme_raw)


@router.get(
    "/database", response_model=DatabaseInfo, summary="当前数据库信息（含当前账号）"
)
def get_database_info(user: GatewayUser = Depends(get_gateway_user)):
    return settings_service.get_database_info(user)


@router.post(
    "/user/claim",
    response_model=UserClaimResult,
    summary="认领历史数据（归入当前账号）",
)
def claim_legacy_bills(user: GatewayUser = Depends(get_gateway_user)):
    """把升级前入库、无归属的历史流水认领到当前飞牛账号（本地模式无网关身份时无需认领）"""
    return settings_service.claim_legacy_bills(user)


@router.post(
    "/database/test", response_model=ConnectionTestResult, summary="测试目标数据库连接"
)
def test_target_database(target: TargetDatabase):
    try:
        return settings_service.test_target_connection(target)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/database/migrate",
    response_model=MigrateResult,
    summary="把现有数据迁移到新数据库并切换",
)
def migrate_database(target: TargetDatabase):
    """搬移现有流水/分类到目标库并立即切换（源数据库保留不动，可回退）"""
    try:
        return settings_service.migrate_and_switch(target)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/logs",
    response_model=RuntimeLog,
    summary="运行日志尾部（含导入/智能分类过程日志）",
)
def get_runtime_logs(
    lines: int = Query(300, ge=10, le=2000, description="返回末尾行数"),
    user: GatewayUser = Depends(get_gateway_user),
):
    return settings_service.get_runtime_logs(lines)


@router.get("/logs/download", summary="下载完整运行日志文件")
def download_runtime_log(user: GatewayUser = Depends(get_gateway_user)):
    path = settings_service.LOG_PATH
    if not path.exists():
        raise HTTPException(status_code=404, detail="日志文件不存在")
    return settings_service.download_runtime_log()


@router.get(
    "/backup",
    summary="下载全量数据备份（JSON，含全部账号，仅管理员）",
    dependencies=[Depends(require_admin)],
)
def download_backup():
    import json
    from datetime import datetime

    data = backup_service.export_backup()
    content = json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f"attachment; filename=fn-finstat-backup-{stamp}.json"
            )
        },
    )


@router.post(
    "/restore",
    response_model=BackupRestoreResult,
    summary="从备份 JSON 恢复数据（默认合并，replace=true 覆盖，仅管理员）",
    dependencies=[Depends(require_admin)],
)
def restore_backup(
    file: UploadFile = File(..., description="备份 JSON 文件"),
    replace: bool = Form(False, description="true=清空后导入（不可恢复）"),
):
    """合并模式按唯一键去重导入；覆盖模式先清空业务表再导入"""
    raw = file.file.read(MAX_UPLOAD_SIZE + 1)
    if len(raw) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="备份文件超过 10MB 大小限制")
    data = backup_service.load_backup_text(raw)
    result = backup_service.restore_backup(data, replace=replace)
    return BackupRestoreResult(**result)
