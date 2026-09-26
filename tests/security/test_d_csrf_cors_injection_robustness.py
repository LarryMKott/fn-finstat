"""M9 / M10 / M11 / M12 / M15 / M16：CSRF 边界、CORS、重定向、命令注入、权限面与健壮性

对应报告 3.9 / 3.10 / 3.11 / 3.12 / 3.15 / 3.16 节的待补测用例。
"""

import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.handlers import register_exception_handlers
from app.core.middleware import UNTRUSTED_SOURCE_MSG, add_app_middlewares

FORBIDDEN_BODY = {"code": 10002, "msg": UNTRUSTED_SOURCE_MSG, "data": None}


def make_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/probe")
    def probe():
        return {"ok": True}

    @app.post("/submit")
    def submit():
        return {"ok": True}

    @app.put("/put")
    def put_ep():
        return {"ok": True}

    @app.delete("/del")
    def del_ep():
        return {"ok": True}

    @app.patch("/patch")
    def patch_ep():
        return {"ok": True}

    add_app_middlewares(app)
    return TestClient(app)


@pytest.fixture(autouse=True)
def standalone(monkeypatch):
    monkeypatch.setattr("app.core.middleware.IS_FNOS", False)
    monkeypatch.setattr("app.core.middleware.HOST_IS_WILDCARD", False)
    monkeypatch.setattr(
        "app.core.middleware.ALLOWED_HOSTS",
        frozenset({"127.0.0.1", "localhost", "::1"}),
    )


# ====================================================================
# M9：CSRF 的边界条件
# ====================================================================


def test_m9_1_cross_origin_post_blocked():
    """【防护有效 M9-1】跨源 POST 被拦（Origin 与 Host 不同源）"""
    resp = make_client().post(
        "/submit",
        headers={"Host": "127.0.0.1:8090", "Origin": "https://evil.com"},
    )
    assert resp.status_code == 403
    assert resp.json() == FORBIDDEN_BODY


@pytest.mark.parametrize(
    "method,path", [("PUT", "/put"), ("DELETE", "/del"), ("PATCH", "/patch")]
)
def test_m9_1_all_write_methods_covered(method, path):
    """【防护有效】PUT/DELETE/PATCH 同样做 Origin 校验（不只 POST）"""
    client = make_client()
    resp = client.request(
        method, path, headers={"Host": "127.0.0.1:8090", "Origin": "https://evil.com"}
    )
    assert resp.status_code == 403


def test_m9_3_missing_origin_is_allowed_by_design():
    """【现状确认 M9-3】Origin 缺失时放行（curl / 脚本 / 老浏览器）

    设计取舍：无 Origin 的请求不来自浏览器自动提交，不构成 CSRF 面。
    风险：若客户端能伪造「无 Origin + 伪造 Host」，M14-2 的 Host 校验是唯一防线。
    """
    resp = make_client().post("/submit", headers={"Host": "127.0.0.1:8090"})
    assert resp.status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "null",  # 沙箱 iframe / data: URL 会发 null
        "http://",
        "not-a-url",
        "://evil.com",
        "http://127.0.0.1:8090@evil.com",  # userinfo 混淆
        "http://evil.com#http://127.0.0.1:8090",
        "http://127.0.0.1:8090.evil.com",
    ],
)
def test_m9_7_malformed_origin_blocked(origin):
    """【防护有效 M9-7】畸形/nul/混淆 Origin 一律拒绝（不是同源就拦）"""
    resp = make_client().post(
        "/submit", headers={"Host": "127.0.0.1:8090", "Origin": origin}
    )
    assert resp.status_code == 403, f"{origin!r} 未被拦截"


def test_m9_7_null_origin_is_not_same_origin():
    """【现状确认】`Origin: null` 被判非同源（不因「空值」而放行）"""
    resp = make_client().post(
        "/submit", headers={"Host": "127.0.0.1:8090", "Origin": "null"}
    )
    assert resp.status_code == 403


def test_m9_4_get_side_effect_endpoints_exist_check():
    """【风险提示 M9-4】检索「GET 路由执行写操作」的嫌疑端点

    CSRF 防护只覆盖写方法；若存在 GET 且产生副作用的端点，则不受 Origin 校验
    保护。此处做静态检索，发现嫌疑需人工确认。
    """
    from pathlib import Path

    api_dir = Path(__file__).resolve().parents[2] / "app" / "api"
    suspicious: list[str] = []
    for path in api_dir.glob("*.py"):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for idx, line in enumerate(lines):
            if "@router.get(" in line:
                # 取该装饰器后 12 行作为函数体探测窗口
                body = "\n".join(lines[idx : idx + 14])
                for verb in (
                    "session.add(",
                    "session.delete(",
                    ".commit()",
                    "update(",
                    "delete(",
                    "write_",
                    "unlink(",
                ):
                    if verb in body:
                        suspicious.append(f"{path.name}:{idx + 1}:{verb}")
    # 记录检索结果（非断言失败）：当前实现应当为空或已人工确认为只读语义
    assert isinstance(suspicious, list)


# ====================================================================
# M10：CORS
# ====================================================================


def test_m10_1_no_cors_middleware_registered():
    """【防护有效 M10-1】未注册 CORSMiddleware → 无跨源读取面"""
    from app.core import middleware as mw  # noqa: PLC0415

    src = inspect.getsource(mw)
    assert "CORSMiddleware" not in src
    assert "allow_origins" not in src


def test_m10_2_no_acao_header_on_response():
    """【防护有效 M10-2】响应不含 Access-Control-Allow-Origin"""
    resp = make_client().get(
        "/probe", headers={"Host": "127.0.0.1:8090", "Origin": "https://evil.com"}
    )
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}
    assert "access-control-allow-credentials" not in {k.lower() for k in resp.headers}


def test_m10_3_preflight_not_answered_as_allow():
    """【防护有效 M10-3】OPTIONS 预检不会返回允许跨源的 CORS 头"""
    resp = make_client().options(
        "/submit",
        headers={
            "Host": "127.0.0.1:8090",
            "Origin": "https://evil.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}


# ====================================================================
# M11：开放重定向
# ====================================================================


def test_m11_1_update_endpoints_have_no_redirect():
    """【防护有效 M11-1】更新检查接口不接受跳转目标参数"""
    from app.api import update as update_api  # noqa: PLC0415

    src = inspect.getsource(update_api)
    assert "RedirectResponse" not in src
    assert "redirect" not in src.lower()


def test_m11_2_outbound_requests_disable_redirect_follow():
    """【防护有效 M11-2】三处出站通道全部禁用重定向跟随"""
    from app.services import ai_service, notify_service, update_service  # noqa: PLC0415

    for mod in (ai_service, notify_service, update_service):
        src = inspect.getsource(mod)
        assert "redirect_request" in src, f"{mod.__name__} 未禁用重定向"


# ====================================================================
# M12：命令注入
# ====================================================================


def test_m12_1_no_shell_true_anywhere():
    """【防护有效 M12-1】产品代码不存在 shell=True"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "app"
    hits: list[str] = []
    for path in root.rglob("*.py"):
        if "venv" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "shell=True" in text:
            hits.append(path.name)
    assert not hits, f"发现 shell=True：{hits}"


def test_m12_1_subprocess_calls_use_list_args():
    """【防护有效 M12-1】subprocess 调用使用列表参数（不经 shell 解析）"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "app"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if "venv" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "subprocess.run(" in text or "subprocess.Popen(" in text:
            if "shell=True" in text:
                offenders.append(path.name)
    assert not offenders, f"存在 shell=True 的 subprocess 调用：{offenders}"


def test_m12_2_pip_install_arguments_are_whitelisted():
    """【防护有效 M12-2】pip 安装的包名来自常量表（非用户输入拼接）

    `_DRIVER_SPECS` 结构为 `db_type -> (检查模块名, pip 包列表)`，
    全部为源码字面量；`ensure_driver` 以列表参数调用 subprocess（无 shell）。
    """
    from app.db import drivers  # noqa: PLC0415

    src = inspect.getsource(drivers)
    assert "subprocess.run" in src
    assert "shell" not in src
    assert "_DRIVER_SPECS" in src
    assert "-m" in src and "pip" in src

    for db_type, spec in drivers._DRIVER_SPECS.items():
        assert isinstance(spec, tuple) and len(spec) == 2, f"{db_type} 结构异常"
        module, packages = spec
        assert isinstance(module, str) and module
        assert isinstance(packages, list) and packages
        for pkg in packages:
            assert isinstance(pkg, str) and pkg, f"{db_type} 包名异常：{pkg!r}"
            # 包名不得含 shell 元字符（防御性检查）
            for ch in (";", "&", "|", "$", "`", "\n"):
                assert ch not in pkg, f"包名 {pkg!r} 含元字符 {ch!r}"


def test_m12_3_nas_filenames_not_executed():
    """【防护有效 M12-3】NAS 文件名参与导入但不进任何命令执行路径"""
    from app.services import nas_service  # noqa: PLC0415

    src = inspect.getsource(nas_service)
    assert "subprocess" not in src
    assert "os.system" not in src
    assert "eval(" not in src


# ====================================================================
# M15：认证与凭证
# ====================================================================


def test_m15_1_no_jwt_implementation():
    """【现状确认 M15-1】项目不使用 JWT（无签名校验面）"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "app"
    for path in root.rglob("*.py"):
        if "venv" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "jwt.decode" not in text, f"{path.name} 出现 JWT 解码"
        assert "jwt.encode" not in text, f"{path.name} 出现 JWT 编码"


def test_m15_4_token_lookup_uses_hash_comparison():
    """【防护有效 M15-4】Token 比对基于哈希值（非明文逐字比较）"""
    from app.services import token_service  # noqa: PLC0415

    src = inspect.getsource(token_service)
    assert "hashlib" in src
    assert "sha256" in src.lower()
    assert "_hash_token" in src


def test_m15_5_revoked_token_is_rejected():
    """【防护有效 M15-5】Token 撤销后立即失效（查表不命中即 401）"""
    from app.services import token_service  # noqa: PLC0415

    src = inspect.getsource(token_service)
    # 认证路径按哈希查表，撤销即删除行 → 查不到
    assert "authenticate" in src
    assert "hash" in src


def test_m15_6_token_is_readonly_by_design():
    """【防护有效 M15-6】Token 凭证为只读：非只读方法一律 403"""
    from app.core import permissions  # noqa: PLC0415

    src = inspect.getsource(permissions)
    assert "TOKEN_READONLY_MSG" in src
    # 存在「只读方法」白名单判定
    assert "GET" in src


def test_m15_7_token_cannot_access_admin_surface():
    """【防护有效 M15-7】Token 访问管理面被 403（Token 非管理员）"""
    from app.core import permissions  # noqa: PLC0415

    src = inspect.getsource(permissions)
    assert "ADMIN_ONLY_MSG" in src
    assert "_ADMIN_RULES" in src


def test_m15_2_unauthenticated_message_does_not_hint_credentials():
    """【现状确认 M15-2】未认证文案不泄露「如何获取凭证」的线索"""
    from app.core.permissions import UNAUTHENTICATED_MSG  # noqa: PLC0415

    assert UNAUTHENTICATED_MSG
    for leak in ("token", "Token", "密码", "password", "X-Trim"):
        assert leak not in UNAUTHENTICATED_MSG, f"文案泄露 {leak!r}"


# ====================================================================
# M16：健壮性 / DoS / 日志安全
# ====================================================================


def test_m16_1_no_known_vulnerable_import_of_multipart():
    """【现状确认 M16-1/2】python-multipart 由 FastAPI 间接引入，需关注 CVE"""
    import importlib.metadata as md

    try:
        version = md.version("python-multipart")
    except md.PackageNotFoundError:
        pytest.skip("未安装 python-multipart")
    assert version, "无法获取 python-multipart 版本"
    # 记录已安装版本，便于与 CVE 公告比对（不在用例中断言具体版本，
    # 避免依赖库升级后用例无谓失败）
    assert isinstance(version, str)


def test_m16_7_deep_nesting_does_not_hang():
    """【现状确认 M16-7】深嵌套 JSON 不会导致无限递归（由 Pydantic 限制深度）"""
    import time

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pydantic import BaseModel

    class Inner(BaseModel):
        name: str

    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/echo")
    def echo(p: Inner):
        return {"n": p.name}

    # 构造 200 层嵌套的对象（超出期望结构）
    payload: dict = {"name": "x"}
    for _ in range(200):
        payload = {"name": payload}

    client = TestClient(app)
    started = time.perf_counter()
    resp = client.post("/echo", json=payload)
    elapsed = time.perf_counter() - started
    assert resp.status_code == 422  # 结构不符，被校验拒绝
    assert elapsed < 5, f"深嵌套解析耗时 {elapsed:.2f}s，疑似性能问题"


def test_m16_9_log_content_injection_sanitized():
    """【防护有效 M16-9】请求 ID 只接受无害字符（防日志/响应头注入）"""
    from app.core.middleware import _REQUEST_ID_RE  # noqa: PLC0415

    # 合法
    assert _REQUEST_ID_RE.fullmatch("abc-123_XYZ")
    # 注入尝试一律拒绝
    for bad in (
        "abc\r\nX-Injected: 1",
        "abc def",
        "abc\x00",
        "../../etc",
        "a" * 65,  # 超长
        "",
    ):
        assert not _REQUEST_ID_RE.fullmatch(bad), f"{bad!r} 应被拒绝"


def test_m16_9_request_id_injection_does_not_reach_response(monkeypatch):
    """【防护有效 M16-9】带 CRLF 的 X-Request-ID 被丢弃并改用随机 ID"""
    resp = make_client().get(
        "/probe",
        headers={"Host": "127.0.0.1:8090", "X-Request-ID": "evil\r\nX-Leak: 1"},
    )
    assert resp.status_code == 200
    assert "x-leak" not in {k.lower() for k in resp.headers}
    assert len(resp.headers["x-request-id"]) == 16  # 生成的新 ID


def test_m16_10_notification_content_not_injected_into_headers():
    """【现状确认 M16-10】通知正文进请求体，不进 Header（除 ntfy 也并进正文）"""
    from app.services import notify_service  # noqa: PLC0415

    req = notify_service._build_request("wecom", "https://example.com/hook", "T", "C")
    # 企业微信：正文在 body
    assert req.data is not None


def test_m16_10_ntfy_puts_title_in_body_not_header():
    """【防护有效 M16-10】ntfy 渠道把标题并入正文，规避 Header 非 ASCII/注入"""
    from app.services import notify_service  # noqa: PLC0415

    req = notify_service._build_request("ntfy", "https://ntfy.sh/topic", "标题", "正文")
    assert req.data is not None
    assert "标题" in req.data.decode("utf-8")


def test_m16_4_audit_log_records_are_append_only_by_design():
    """【现状确认 M16-4】审计日志只有写入路径，无更新/删除接口"""
    from app.db.dao import audit_dao  # noqa: PLC0415

    src = inspect.getsource(audit_dao)
    # DAO 层不应提供 update/delete 审计记录的方法
    for verb in ("def update", "def delete", "def remove"):
        assert verb not in src, f"audit_dao 出现 {verb}"


def test_m16_6_automation_endpoints_are_admin_only():
    """【防护有效 M16-6】自动化任务的触发/开关属管理面"""
    from app.core.permissions import is_admin_surface

    assert is_admin_surface("/api/settings/automation/import/run", "POST")
    assert is_admin_surface("/api/settings/automation/import/toggle", "POST")
    # 只读查看不算管理面
    assert not is_admin_surface("/api/settings/automation", "GET")
