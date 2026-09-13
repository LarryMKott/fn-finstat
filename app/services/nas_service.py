"""NAS 目录导入业务：配置账单目录、浏览目录（识别来源）、按文件导入

目录安全：所有访问路径必须位于配置的账单目录之内（resolve 后校验祖先关系，
杜绝 .. 与符号链接越界）；单文件大小上限与上传一致。
导入复用 import_service.import_local_file 管线，来源由 parsers.detect 识别。
"""

import logging
from pathlib import Path

from app.config import (
    MAX_UPLOAD_SIZE_MB,
    NAS_IMPORT_EXTS,
    NAS_MAX_FILE_SIZE,
    NASImportSettings,
    load_nas_settings,
    save_nas_settings,
)
from app.core.errors import (
    ErrorCode,
    NotFoundError,
    UploadTooLargeError,
    ValidationError,
)
from app.parsers import build_parser
from app.parsers import detect
from app.schemas.nas import (
    NasConfigOut,
    NasConfigUpdate,
    NasDirectory,
    NasEntry,
)
from app.schemas.upload import ImportResult
from app.services import import_service

logger = logging.getLogger(__name__)


def get_config() -> NasConfigOut:
    """当前账单目录配置（附目录可访问状态，前端据此提示）"""
    import_dir = load_nas_settings().import_dir
    return NasConfigOut(
        import_dir=import_dir,
        exists=bool(import_dir) and Path(import_dir).is_dir(),
        supported_exts=list(NAS_IMPORT_EXTS),
    )


def update_config(payload: NasConfigUpdate, owner_user_id: str = "") -> NasConfigOut:
    """保存账单目录：仅接受绝对路径（资源管理器复制的地址常带引号，顺手剥掉）

    记录配置者账号（owner_user_id）：目录监听自动导入的流水归入该账号。
    """
    import_dir = payload.import_dir.strip().strip('"').strip()
    if not import_dir:
        raise ValidationError("账单目录不能为空", code=ErrorCode.NAS_DIR_INVALID)
    path = Path(import_dir).expanduser()
    if not path.is_absolute():
        raise ValidationError("账单目录必须是绝对路径", code=ErrorCode.NAS_DIR_INVALID)
    settings = NASImportSettings(
        import_dir=str(path),
        owner_user_id=owner_user_id or load_nas_settings().owner_user_id,
    )
    save_nas_settings(settings)
    logger.info("账号更新 NAS 账单目录：%s", settings.import_dir)
    return NasConfigOut(
        import_dir=settings.import_dir,
        exists=path.is_dir(),
        supported_exts=list(NAS_IMPORT_EXTS),
    )


def _require_root() -> Path:
    """取配置的账单目录；未配置或目录不可访问时给出可操作的错误提示"""
    import_dir = load_nas_settings().import_dir
    if not import_dir:
        raise ValidationError(
            "请先在导入页设置 NAS 账单目录", code=ErrorCode.NAS_DIR_INVALID
        )
    root = Path(import_dir)
    if not root.is_dir():
        raise ValidationError(
            f"账单目录不存在或不可访问：{import_dir}", code=ErrorCode.NAS_DIR_INVALID
        )
    return root


def _resolve_in_root(root: Path, rel: str) -> Path:
    """把目录内相对路径解析为绝对路径，并确保仍位于账单目录内"""
    base = root.resolve()
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        raise ValidationError(
            "访问路径超出账单目录范围", code=ErrorCode.NAS_DIR_INVALID
        )
    return target


def _rel_of(base: Path, target: Path) -> str:
    """条目相对账单目录的路径（统一 / 分隔，前端原样回传）"""
    return target.relative_to(base).as_posix()


def list_directory(rel: str = "") -> NasDirectory:
    """浏览账单目录：返回子目录与账单文件（文件附识别出的来源）"""
    root = _require_root()
    base = root.resolve()
    target = _resolve_in_root(base, rel)
    if not target.is_dir():
        raise ValidationError("该路径不是目录", code=ErrorCode.NAS_DIR_INVALID)

    dirs: list[NasEntry] = []
    files: list[NasEntry] = []
    try:
        entries = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        raise ValidationError(
            f"读取目录失败：{exc}", code=ErrorCode.NAS_DIR_INVALID
        ) from exc
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                dirs.append(
                    NasEntry(name=entry.name, path=_rel_of(base, entry), is_dir=True)
                )
            elif entry.is_file() and entry.suffix.lower() in NAS_IMPORT_EXTS:
                stat = entry.stat()
                files.append(
                    NasEntry(
                        name=entry.name,
                        path=_rel_of(base, entry),
                        is_dir=False,
                        size=stat.st_size,
                        modified=stat.st_mtime,
                        source=detect.detect_source(entry) or "unknown",
                    )
                )
        except OSError:
            continue  # 无权限/已被删除的条目跳过，不影响其余展示
    if target != base and target.parent != base:
        parent_rel = _rel_of(base, target.parent)
    else:
        # 根目录或一级子目录：上一级就是账单目录根
        parent_rel = ""
    return NasDirectory(
        root=str(root),
        path=_rel_of(base, target) if target != base else "",
        parent=parent_rel,
        dirs=dirs,
        files=files,
    )


def import_file(rel: str, user_id: str) -> ImportResult:
    """导入账单目录中的单个文件：来源识别 → 复用通用导入管线（不去删除源文件）"""
    root = _require_root()
    target = _resolve_in_root(root, rel)
    if not target.is_file():
        raise NotFoundError("文件不存在或已被移动")
    if target.stat().st_size > NAS_MAX_FILE_SIZE:
        raise UploadTooLargeError(f"文件大小超过 {MAX_UPLOAD_SIZE_MB}MB 限制")
    if target.stat().st_size == 0:
        raise ValidationError("文件内容为空", code=ErrorCode.IMPORT_PARSE_FAILED)

    source = detect.detect_source(target)
    if not source:
        raise ValidationError(
            "无法识别账单来源，请确认是微信/支付宝/京东/云闪付导出的原始账单文件",
            code=ErrorCode.IMPORT_SOURCE_UNKNOWN,
        )
    parser = build_parser(source)
    if parser is None:  # 识别与解析器注册表不同步时的防御分支
        raise ValidationError(
            "该来源暂不支持导入", code=ErrorCode.IMPORT_SOURCE_UNKNOWN
        )

    logger.info("NAS 导入 %s（识别来源 %s）", rel, source)
    return import_service.import_local_file(target, parser, target.name, user_id)
