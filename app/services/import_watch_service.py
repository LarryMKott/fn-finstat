"""NAS 账单目录监听自动导入（T-5.3）：定时扫描目录，指纹比对后导入新/变更文件

关键设计（docs/devlog 开发计划 T-5.3）：
- 判重不能只靠文件名：用户用同名文件覆盖（重新导出同名账单）很常见，
  以「内容 sha256 指纹」为准，指纹一致即跳过（含同名覆盖场景）
- 同内容失败文件不无限重试：失败结果也登记指纹，仅当内容变化时重新处理
- 绝不影响主流程：单文件失败只记录，不影响其余文件与调度循环
- 沿用既有安全边界：路径限制在配置目录内、单文件 10MB 上限、
  仅支持已注册的账单扩展名（复用 nas_service 的目录解析）

调度契约：scan_and_import() 返回 (affected, message)，未配置目录时为
(0, 提示语) 的空转成功，避免被调度器误判为连续失败而自动停用。
"""

import hashlib
import logging
from pathlib import Path

from app.config import (
    NAS_IMPORT_EXTS,
    NAS_MAX_FILE_SIZE,
    load_nas_settings,
)
from app.db.dao.task_dao import ImportedFileDAO, path_key_of
from app.parsers import build_parser, detect
from app.services import import_service

logger = logging.getLogger(__name__)

TASK_KEY = "nas_watch"

# 单轮扫描的候选文件数上限：目录极深或混入大量非账单文件时避免长期占用扫描线程
MAX_CANDIDATES = 2000


def _file_hash(path: Path) -> str:
    """文件内容 sha256 指纹（账单文件上限 10MB，全量读取成本可忽略）"""
    digest = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stat_safe(path: Path) -> tuple[int, float]:
    """文件 size/mtime；取不到（被并发删除/无权限）时降级为 0，失败登记仍可落库"""
    try:
        st = path.stat()
        return st.st_size, st.st_mtime
    except OSError:
        return 0, 0.0


def _candidate_files(root: Path) -> list[Path]:
    """目录树内所有受支持的账单文件（跳过隐藏文件，大小合规，不越出目录）

    符号链接逃逸防护：resolve 后必须仍位于配置目录内 —— 目录里放一个指向
    目录外文件的软链即可绕过文件名约束，与 nas_service 的安全边界保持一致。
    """
    root_resolved = root.resolve()
    candidates = []
    for path in sorted(root.rglob("*")):
        if len(candidates) >= MAX_CANDIDATES:
            logger.warning(
                "目录监听：候选文件数达到上限 %d，本轮截断扫描", MAX_CANDIDATES
            )
            break
        try:
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in NAS_IMPORT_EXTS:
                continue
            size = path.stat().st_size
            if size == 0 or size > NAS_MAX_FILE_SIZE:
                continue
            if root_resolved not in path.resolve().parents:
                logger.warning("目录监听：跳过越出配置目录的符号链接 %s", path)
                continue
            candidates.append(path)
        except OSError:
            continue  # 无权限/已被删除的文件跳过
    return candidates


def scan_and_import() -> tuple[int, str]:
    """扫描账单目录：新文件 / 内容变更文件走导入管线，结果逐文件登记

    返回 (新增流水总数, 摘要)；目录未配置或不可访问时空转成功。
    """
    nas_settings = load_nas_settings()
    import_dir = nas_settings.import_dir
    if not import_dir:
        return 0, "未配置账单目录，跳过扫描"
    root = Path(import_dir)
    if not root.is_dir():
        return 0, f"账单目录不存在或不可访问：{import_dir}"

    owner = nas_settings.owner_user_id
    stats = {"new": 0, "changed": 0, "unchanged": 0, "failed": 0, "unknown": 0}
    inserted_total = 0
    candidates = _candidate_files(root)
    # 一轮一次批量取全部既有指纹，替代逐文件查询的 N+1 往返
    known_hashes = ImportedFileDAO.content_hashes(
        [path_key_of(str(p)) for p in candidates]
    )
    for path in candidates:
        # 先置空再进 try：_file_hash 抛异常时 except 分支仍能引用它
        content_hash = ""
        size, mtime = _stat_safe(path)
        try:
            content_hash = _file_hash(path)
            if known_hashes.get(path_key_of(str(path))) == content_hash:
                # 同内容（无论上次成功与否）不重复处理；变更后才会重试
                stats["unchanged"] += 1
                continue
            is_change = path_key_of(str(path)) in known_hashes
            source = detect.detect_source(path)
            if not source:
                stats["unknown"] += 1
                ImportedFileDAO.upsert(
                    str(path),
                    file_name=path.name,
                    user_id=owner,
                    size=size,
                    mtime=mtime,
                    content_hash=content_hash,
                    status="unknown",
                    message="无法识别账单来源",
                )
                logger.info("目录监听：无法识别来源，已登记跳过 %s", path.name)
                continue
            parser = build_parser(source)
            if parser is None:
                stats["unknown"] += 1
                ImportedFileDAO.upsert(
                    str(path),
                    file_name=path.name,
                    user_id=owner,
                    size=size,
                    mtime=mtime,
                    content_hash=content_hash,
                    status="unknown",
                    message="来源已识别但解析器缺失",
                )
                logger.warning(
                    "目录监听：来源 %s 无解析器，已登记跳过 %s", source, path.name
                )
                continue
            result = import_service.import_local_file(path, parser, path.name, owner)
            inserted_total += result.inserted
            stats["changed" if is_change else "new"] += 1
            ImportedFileDAO.upsert(
                str(path),
                file_name=path.name,
                user_id=owner,
                size=size,
                mtime=mtime,
                content_hash=content_hash,
                status="ok",
                message=f"新增 {result.inserted} 条 / 跳过 {result.skipped} 条",
                inserted=result.inserted,
            )
            logger.info(
                "目录监听导入 %s（%s）：新增 %s 跳过 %s",
                path.name,
                source,
                result.inserted,
                result.skipped,
            )
        except Exception as exc:  # 单文件失败只记录，不中断扫描
            stats["failed"] += 1
            logger.warning("目录监听导入失败 %s：%s", path, exc)
            try:
                ImportedFileDAO.upsert(
                    str(path),
                    file_name=path.name,
                    user_id=owner,
                    size=size,
                    mtime=mtime,
                    content_hash=content_hash,
                    status="failed",
                    message=f"{type(exc).__name__}: {exc}",
                )
            except Exception:  # 登记失败也不影响整体扫描
                logger.exception("目录监听结果登记失败：%s", path)
    summary = (
        f"新增文件 {stats['new']}，变更文件 {stats['changed']}，"
        f"未变化 {stats['unchanged']}，无法识别 {stats['unknown']}，"
        f"失败 {stats['failed']}；新增流水 {inserted_total} 条"
    )
    logger.info("目录监听扫描完成：%s", summary)
    return inserted_total, summary
