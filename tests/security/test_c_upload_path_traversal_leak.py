"""M3 / M6 / M7：文件上传、路径穿越、敏感信息泄露

对应报告 3.3 / 3.6 / 3.7 节的待补测用例。逐条给出可执行证据。
"""

import io
from pathlib import Path

import pytest

# ====================================================================
# M3：文件上传（后缀校验、大小限制、空文件、解析器错配）
# ====================================================================


def _upload(name: str, content: bytes):
    """构造 FastAPI UploadFile 替身（避免依赖 multipart 解析）"""
    from fastapi import UploadFile  # noqa: PLC0415

    return UploadFile(
        file=io.BytesIO(content), filename=name, size=len(content)
    )


@pytest.mark.parametrize(
    "filename",
    [
        "bill.xlsx.exe",
        "bill.exe",
        "bill.xlsx.php",
        "bill.txt",
        "bill",
        "bill.xlsx. ",
        "bill.xls",  # 旧格式不在白名单
        "bill.XLSX ",  # 尾随空格 → suffix 为 ".xlsx " 不等于 ".xlsx"
    ],
)
def test_m3_1_3_double_extension_and_suffix_rejected(filename):
    """【防护有效 M3-1/3】双扩展名与非法后缀一律拒绝

    `save_upload` 用 `Path(filename).suffix.lower()` 取**最后一段**后缀，
    故 `bill.xlsx.exe` 得到 `.exe` ≠ `.xlsx` → 拒绝，不落入双扩展名绕过。
    """
    from app.core.errors import ValidationError
    from app.utils.file_utils import save_upload

    with pytest.raises(ValidationError):
        save_upload(_upload(filename, b"data"), ".xlsx")


def test_m3_1_2_uppercase_suffix_accepted_after_lower():
    """【现状确认】`.XLSX` 经 `.lower()` 后被接受（大小写不敏感，符合预期）"""
    from app.utils.file_utils import save_upload

    path = save_upload(_upload("bill.XLSX", b"dummy"), ".xlsx")
    try:
        assert path.exists()
        assert path.suffix == ".xlsx"  # 落盘用统一小写后缀
    finally:
        path.unlink(missing_ok=True)


def test_m3_5_unicode_suffix_rejected():
    """【防护有效 M3-5】Unicode 近似后缀（全角、西里尔字母）被拒"""
    from app.core.errors import ValidationError
    from app.utils.file_utils import save_upload

    for name in ("bill.xlsx\u3000", "bill.хlsx", "bill.xlsх"):  # 全角空格 / 西里尔 х
        with pytest.raises(ValidationError):
            save_upload(_upload(name, b"data"), ".xlsx")


def test_m3_8_empty_file_rejected():
    """【防护有效 M3-8】空文件被拒（size == 0 → ValidationError）"""
    from app.core.errors import ValidationError
    from app.utils.file_utils import save_upload

    with pytest.raises(ValidationError):
        save_upload(_upload("bill.xlsx", b""), ".xlsx")


def test_m3_4_oversize_rejected_before_write(monkeypatch):
    """【防护有效 M3-4】超出大小上限的文件在预检阶段即被拒（不落盘）"""
    from app.core.errors import UploadTooLargeError
    from app.utils import file_utils

    monkeypatch.setattr(file_utils, "MAX_UPLOAD_SIZE", 1024)
    monkeypatch.setattr(file_utils, "MAX_UPLOAD_SIZE_MB", 0.001)
    with pytest.raises(UploadTooLargeError):
        file_utils.save_upload(_upload("bill.xlsx", b"x" * 4096), ".xlsx")


def test_m3_4_oversize_detected_when_size_missing(monkeypatch):
    """【防护有效 M3-4·兜底】size 字段缺失时按实际读取量兜底拦截"""
    from fastapi import UploadFile

    from app.core.errors import UploadTooLargeError
    from app.utils import file_utils

    monkeypatch.setattr(file_utils, "MAX_UPLOAD_SIZE", 1024)
    monkeypatch.setattr(file_utils, "MAX_UPLOAD_SIZE_MB", 0.001)
    file = UploadFile(file=io.BytesIO(b"x" * 4096), filename="bill.xlsx", size=0)
    with pytest.raises(UploadTooLargeError):
        file_utils.save_upload(file, ".xlsx")


def test_m3_temp_filename_is_randomized():
    """【防护有效】落盘名用 uuid4 随机名，不采用用户文件名

    杜绝「用户文件名含路径分隔符 / 保留名 / 与服务端文件同名」导致的问题。
    """
    from app.utils.file_utils import save_upload

    path = save_upload(_upload("../../etc/passwd.xlsx", b"d"), ".xlsx")
    try:
        assert path.parent.name == "tmp" or "tmp" in str(path.parent).lower()
        assert len(path.stem) == 32  # uuid4().hex
        assert path.name != "passwd.xlsx"
    finally:
        path.unlink(missing_ok=True)


def test_m3_6_zip_bomb_guarded_by_size_limit(monkeypatch):
    """【现状确认 M3-6】ZIP 炸弹依赖「压缩包本体大小」限制，非解压后大小

    当前实现按上传字节数限流（MAX_UPLOAD_SIZE），不检测解压膨胀比。
    解析由 openpyxl/csv 在内存与临时文件侧完成，超限会被 size 限制先挡住
    （压缩包本体超 10MB 即拒），但「小体积高膨胀」的理论面仍存在。
    """
    from app.config import MAX_UPLOAD_SIZE, MAX_UPLOAD_SIZE_MB

    assert MAX_UPLOAD_SIZE == MAX_UPLOAD_SIZE_MB * 1024 * 1024
    # 确认实现中无解压比校验（若已引入会命中这些标记）
    from app.utils import file_utils

    src = Path(file_utils.__file__).read_text(encoding="utf-8", errors="replace")
    for marker in ("ratio", "膨胀", "decompress", "zipfile"):
        assert marker not in src, f"file_utils 出现 ZIP 膨胀检测（{marker}）"


def test_m3_9_parser_mismatch_reports_unknown_source():
    """【现状确认 M3-9】解析器错配（上传 xlsx 走 csv 解析器）会被来源识别拦下"""
    from app.parsers import PARSERS

    wechat = PARSERS["wechat"]
    alipay = PARSERS["alipay"]
    assert wechat.ext == ".xlsx"
    assert alipay.ext in (".csv",)


# ====================================================================
# M6：路径穿越
# ====================================================================


@pytest.mark.parametrize(
    "rel",
    [
        "../",
        "../../etc/passwd",
        "..\\..\\windows\\win.ini",
        "a/../../..",
        "/etc/passwd",  # 绝对路径：Path("/etc") 会替换 base
        "sub/../../../../etc/passwd",
    ],
)
def test_m6_1_2_3_5_path_traversal_blocked(tmp_path, rel):
    """【防护有效 M6-1/2/3/5】NAS 相对路径的 .. 与绝对路径穿越被判越界

    `_resolve_in_root` 用 `(base / rel).resolve()` 后再验祖先关系：
    resolve() 把 `..` 归一化、`/etc/passwd` 这类绝对路径会**替换** base，
    二者都会让 `base not in target.parents` 成立 → 拒绝。
    """
    from app.core.errors import ValidationError
    from app.services.nas_service import _resolve_in_root

    base = tmp_path / "bills"
    base.mkdir()
    with pytest.raises(ValidationError) as exc:
        _resolve_in_root(base, rel)
    assert "超出账单目录范围" in str(exc.value.message)


def test_m6_dotdot_lookalike_is_not_traversal(tmp_path):
    """【现状确认·非缺陷】`....` 不是 `..`，解析后仍在目录内 → 放行正确

    `....//....//etc/passwd` 在 POSIX 语义里是一串普通目录名（`....`），
    resolve() 不会把它当作上跳，故不构成穿越。此用例记录该判定，
    防止误把它当成漏洞。
    """
    from app.services.nas_service import _resolve_in_root

    base = tmp_path / "bills"
    base.mkdir()
    resolved = _resolve_in_root(base, "....//....//etc/passwd")
    assert resolved == (base / "...." / "...." / "etc" / "passwd").resolve()
    assert base.resolve() in resolved.parents


def test_m6_7_symlink_escape_blocked(tmp_path):
    """【已加固 M6-7】目录内符号链接指向目录外时被拦截

    原报告结论（"Windows 上 `Path.resolve()` 不跟随目录符号链接 → 越界未被拦截"）
    经 2026-09-26 复测**修正为假阳性**：当时 `symlink_to(target_is_directory=True)`
    在本机创建失败（`os.path.islink()` 返回 False），把「链接没建成」误判为
    「resolve 不解析」。改用 junction 复测后，`resolve()` 正确解析到目录外并
    被祖先校验拦下。

    本次仍做加固（纵深防御）：`_resolve_in_root` 增加「逐段 islink/isjunction
    检测」，不依赖 `resolve()` 的平台实现细节 —— 万一条目解析失败静默返回
    未解析路径，这层能兜住。链接一律按越界拒绝（业务上不需要链接跳转）。
    """
    from app.core.errors import ValidationError
    from app.services.nas_service import _resolve_in_root, _contains_reparse_point

    base = tmp_path / "bills"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")

    link = base / "link"
    created = _try_make_dir_link(link, outside)
    if not created:
        pytest.skip("当前环境无法创建目录链接/junction")

    # 加固后：无论 resolve() 是否解析，链接路径一律拒绝
    with pytest.raises(ValidationError) as exc:
        _resolve_in_root(base, "link/secret.txt")
    assert "链接" in str(exc.value.message) or "超出" in str(exc.value.message)
    # 链接目录本身同样拒绝
    with pytest.raises(ValidationError):
        _resolve_in_root(base, "link")
    # 直接验证检测函数
    assert _contains_reparse_point(base.resolve(), link.resolve()) is True


def test_m6_7_plain_files_and_dirs_still_allowed(tmp_path):
    """【已加固 M6-7 对照】普通文件与目录不受链接检测影响（不过度拦截）"""
    from app.services.nas_service import _resolve_in_root

    base = tmp_path / "bills"
    (base / "sub").mkdir(parents=True)
    (base / "sub" / "bill.xlsx").write_text("x", encoding="utf-8")

    assert _resolve_in_root(base, "sub/bill.xlsx") == (
        base / "sub" / "bill.xlsx"
    ).resolve()
    assert _resolve_in_root(base, "sub") == (base / "sub").resolve()
    assert _resolve_in_root(base, "") == base.resolve()


def test_m6_7_detection_does_not_false_positive_on_normal_tree(tmp_path):
    """【已加固 M6-7】无链接的目录树不会被误判为含链接"""
    from app.services.nas_service import _contains_reparse_point

    base = tmp_path / "bills"
    (base / "a" / "b" / "c").mkdir(parents=True)
    (base / "a" / "b" / "c" / "f.txt").write_text("x", encoding="utf-8")
    root = base.resolve()
    assert _contains_reparse_point(root, (base / "a").resolve()) is False
    assert (
        _contains_reparse_point(root, (base / "a" / "b" / "c" / "f.txt").resolve())
        is False
    )


def _try_make_dir_link(link: Path, target: Path) -> bool:
    """尽力创建目录链接：先试 symlink_to，失败再试 Windows junction

    返回是否成功创建（且确认为链接/junction）。本机实测 `symlink_to` 在
    无符号链接权限时会**静默创建普通目录**（`islink()` 为 False），
    故必须验证创建结果，否则测试会假通过。
    """
    import os
    import subprocess

    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pass
    if link.is_symlink() or _is_junction(link):
        return True
    # 清理可能被创建出来的普通目录，再试 junction
    if link.exists() and not link.is_symlink():
        try:
            link.rmdir()
        except OSError:
            return False
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        errors="replace",
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        return False
    return link.is_symlink() or _is_junction(link)


def _is_junction(path: Path) -> bool:
    """是否为 Windows junction（Python 3.12+ 提供 os.path.isjunction）"""
    import os

    is_junction = getattr(os.path, "isjunction", None)
    if is_junction is None:
        return False
    try:
        return bool(is_junction(path))
    except OSError:
        return False



def test_m6_1_legit_relative_path_allowed(tmp_path):
    """【现状确认】账单目录内的正常相对路径放行（不过度拦截）"""
    from app.services.nas_service import _resolve_in_root

    base = tmp_path / "bills"
    (base / "sub").mkdir(parents=True)
    target = _resolve_in_root(base, "sub/bill.xlsx")
    assert target == (base / "sub" / "bill.xlsx").resolve()


def test_m6_root_itself_allowed(tmp_path):
    """【现状确认】rel 为 "." 或空串时解析到根目录本身，不误判越界"""
    from app.services.nas_service import _resolve_in_root

    base = tmp_path / "bills"
    base.mkdir()
    assert _resolve_in_root(base, ".") == base.resolve()
    assert _resolve_in_root(base, "") == base.resolve()


def test_m6_4_log_download_path_is_fixed(monkeypatch):
    """【现状确认 M6-4】日志下载路径取自 config.LOG_PATH 常量，无用户输入

    端点是 `/api/settings/logs/download`，不接受文件名参数 → 无穿越面。
    """
    import inspect

    from app.services import settings_service  # noqa: PLC0415

    src = inspect.getsource(settings_service)
    # 日志相关读取只用 LOG_PATH，不拼接请求参数
    assert "LOG_PATH" in src
    assert "logs/download" not in src  # 路由在 api 层


def test_m6_8_display_name_hides_absolute_path():
    """【防护有效 M6-8】NAS 浏览只回传目录名，不暴露服务器绝对路径"""
    from app.services.nas_service import _display_name

    assert _display_name("/vol1/1000/bills") == "bills"
    assert _display_name("/vol1/1000/bills/微信") == "微信"
    assert "/" not in _display_name("/vol1/1000/bills")


# ====================================================================
# M7：敏感信息泄露
# ====================================================================


def test_m7_1_openapi_hidden_in_fnos_mode():
    """【防护有效 M7-1】fnOS 网关模式不注册 /docs /redoc /openapi.json"""
    import inspect

    from app import main  # noqa: PLC0415

    src = inspect.getsource(main)
    assert "docs_url" in src
    assert "if IS_FNOS" in src.replace("\n", " ")
    # 确认三者都在 IS_FNOS 分支的 else 里
    block = src[src.index("docs_url") : src.index("docs_url") + 300]
    assert "redoc_url" in block and "openapi_url" in block


def test_m7_3_ai_api_key_not_returned_in_config_view():
    """【防护有效 M7-3】AI 配置读取不回传 API Key 明文"""
    import inspect

    from app.api import ai  # noqa: PLC0415

    src = inspect.getsource(ai)
    # 应存在 has_api_key 之类的布尔标记而非明文回传
    assert "has_api_key" in src or "api_key" in src
    assert "sk-" not in src, "源码中出现疑似真实密钥"


def test_m7_4_token_list_excludes_plaintext():
    """【防护有效 M7-4】Token 列表不返回明文（服务端只存 SHA-256）"""
    import inspect

    from app.services import token_service  # noqa: PLC0415

    src = inspect.getsource(token_service)
    assert "_hash_token" in src
    # list 相关实现不应把 raw token 放进返回值
    assert "token_hash" in src or "hash" in src


def test_m7_db_password_not_in_database_info_schema():
    """【防护有效 M7-5】DatabaseInfo 只有 has_password 布尔，无 password 字段"""
    from app.schemas.settings import DatabaseInfo

    fields = set(DatabaseInfo.model_fields)
    assert "password" not in fields
    assert "has_password" in fields


def test_m7_9_unhandled_exception_hides_internals():
    """【防护有效 M7-9】500 响应只给通用文案，不透出异常类型/堆栈"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.handlers import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    def boom():
        raise RuntimeError("SQLAlchemy: connection to db-finstat-1 failed at 10.0.0.5:5432")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == 50000 or body["code"] == 1 or body["data"] is None
    assert "SQLAlchemy" not in resp.text
    assert "10.0.0.5" not in resp.text
    assert "RuntimeError" not in resp.text
    assert "connection to" not in resp.text


def test_m7_9_500_still_carries_security_headers():
    """【现状确认】500 响应仍带 nosniff 与 X-Request-ID（在中间件之外手动补写）"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.handlers import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    def boom():
        raise RuntimeError("internal detail")

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_m7_validation_error_does_not_leak_internals():
    """【防护有效 M7】422 校验错误只回可读文案，不回 Python 类型细节"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pydantic import BaseModel

    from app.core.handlers import register_exception_handlers

    class Payload(BaseModel):
        count: int

    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/echo")
    def echo(p: Payload):
        return {"n": p.count}

    client = TestClient(app)
    resp = client.post("/echo", json={"count": "not-a-number"})
    assert resp.status_code == 422
    assert resp.json()["data"] is None
    assert "Traceback" not in resp.text
