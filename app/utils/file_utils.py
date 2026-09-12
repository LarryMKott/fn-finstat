"""上传文件处理：后缀校验、10MB 大小限制、临时目录保存"""

import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from app.config import MAX_UPLOAD_SIZE, TMP_DIR


def save_upload(file: UploadFile, allowed_ext: str) -> Path:
    """校验后缀与大小，保存到临时目录并返回文件路径"""
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()
    if ext != allowed_ext:
        raise HTTPException(status_code=400, detail=f"仅支持 {allowed_ext} 格式文件")
    # size 预检：超限文件不读入内存（size 缺失时由读后校验兜底）
    if file.size and file.size > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="文件大小超过 10MB 限制")
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(data) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="文件大小超过 10MB 限制")
    target = TMP_DIR / f"{uuid.uuid4().hex}{ext}"
    target.write_bytes(data)
    return target
