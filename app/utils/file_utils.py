"""上传文件处理：后缀校验、大小限制、临时目录保存；附件下载响应头

本模块不依赖 Web 框架（UploadFile 仅作类型标注，运行期以鸭子类型访问
.filename/.size/.file），业务失败统一抛 core.errors.ValidationError。
"""

import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from app.config import MAX_UPLOAD_SIZE, MAX_UPLOAD_SIZE_MB, TMP_DIR
from app.core.errors import ValidationError, UploadTooLargeError

if TYPE_CHECKING:  # 仅类型标注，避免 utils 层运行期依赖 fastapi
    from fastapi import UploadFile

_UPLOAD_CHUNK = 1024 * 1024  # 流式落盘的块大小（1MB）
_UNSAFE_HEADER_CHARS = '"\\\r\n'  # 会破坏 Content-Disposition 结构的字符


def save_upload(file: "UploadFile", allowed_ext: str) -> Path:
    """校验后缀与大小，流式保存到临时目录并返回文件路径

    分块读写避免整个文件读入内存；超限/写入中途失败时清理半成品临时文件。
    """
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    if ext != allowed_ext:
        raise ValidationError(f"仅支持 {allowed_ext} 格式文件")
    # size 预检：超限文件不落盘（size 缺失时由读后校验兜底）
    if file.size and file.size > MAX_UPLOAD_SIZE:
        raise UploadTooLargeError(f"文件大小超过 {MAX_UPLOAD_SIZE_MB}MB 限制")
    target = TMP_DIR / f"{uuid.uuid4().hex}{ext}"
    size = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = file.file.read(_UPLOAD_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    raise UploadTooLargeError(
                        f"文件大小超过 {MAX_UPLOAD_SIZE_MB}MB 限制"
                    )
                out.write(chunk)
        if size == 0:
            raise ValidationError("文件内容为空")
    except Exception:
        try:
            target.unlink(missing_ok=True)
        except OSError:  # 清理失败不影响原始异常上抛
            pass
        raise
    return target


def _sanitize_header_name(name: str) -> str:
    """剥离会破坏响应头结构的字符（引号/反斜杠/CR/LF），防响应头注入"""
    return "".join(ch for ch in name if ch not in _UNSAFE_HEADER_CHARS).strip()


def _sanitize_header_value(value: str) -> str:
    """剥离所有控制字符（含 CR/LF 等非打印字符），防响应头注入"""
    return "".join(ch for ch in value if ch >= " " and ch != "").strip()


def content_disposition(filename: str) -> str:
    """附件下载 Content-Disposition 响应头

    纯 ASCII 文件名直接引用；含非 ASCII（如中文导出文件名）时按 RFC 5987
    补充 filename* 编码字段（filename 字段同时保留，供不识别该标准的旧客户端兜底）。
    引号、反斜杠与 CR/LF 一律剥离，避免响应头注入。
    """
    if filename.isascii():
        safe = _sanitize_header_name(filename) or "export"
        return f'attachment; filename="{safe}"'
    encoded = quote(filename)
    fallback = (
        _sanitize_header_name(Path(filename).stem.encode("ascii", "ignore").decode())
        or "export"
    )
    # 后缀同样走消毒：Path 的 suffix 会原样携带 CR/LF 等控制字符
    suffix = _sanitize_header_value(Path(filename).suffix)
    return f'attachment; filename="{fallback}{suffix}"; ' f"filename*=UTF-8''{encoded}"
