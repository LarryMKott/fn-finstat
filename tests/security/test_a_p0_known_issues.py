"""M14-2 / M5-2 / M5-3：来源校验与 SSRF 的 P0 缺陷用例

报告第四节把这三条列为 ❌ 确认存在的缺陷（P0）。三条均**已修复**，本文件
同时承担「缺陷复现记录」（docstring 中保留修复前行为）与「回归护栏」职责：

- M14-2：`HOST=0.0.0.0` → `config.HOST_IS_WILDCARD=True` →
  `SourceGuardMiddleware` 曾整段跳过 Host 白名单，攻击者只需把自己的域名
  解析到该主机（DNS rebinding），即可用伪造的 Host 头直读接口数据。
  **修复方案**：通配绑定时白名单取「回环 + 本机全部网卡 IP」，域名一律拒绝
  （局域网按 IP 访问不受影响）；逃逸开关 `FNOS_ALLOW_HOSTNAME_WHEN_WILDCARD=1`。
- M5-2 / M5-3：`notify_service.send_webhook` 只校验 URL 以 http(s):// 开头，
  没有内网地址黑名单。持有通知配置写权限的一方（管理员）可借此探测内网
  端口与云元数据地址。**修复方案**：`app/utils/net_guard.validate_outbound_url`
  解析目标 IP 并拒绝私网/回环/链路本地/云元数据。
- M5-7（与 M5-2/3 同源）：AI 通道 `base_url` 同样只校验 scheme，且该通道会把
  API Key 以 Bearer 头发往目标地址 —— 风险最高。**修复方案**：保存侧
  （`api/ai.py`）与出站侧（`ai_service._chat`）双重校验。

用例标注：【已修复】= 修复后的回归护栏；【现状确认】= 一直成立的行为。
本文件跑**真实实现**，不使用 `outbound_guard_bypass` 替身。
"""

import urllib.request

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.handlers import register_exception_handlers
from app.core.middleware import UNTRUSTED_SOURCE_MSG, add_app_middlewares

FORBIDDEN_BODY = {"code": 10002, "msg": UNTRUSTED_SOURCE_MSG, "data": None}


def make_probe_client() -> TestClient:
    """最小探针应用：挂完整中间件栈，不依赖业务路由与数据库"""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/probe")
    def probe():
        return {"ok": True}

    @app.post("/submit")
    def submit():
        return {"ok": True}

    add_app_middlewares(app)
    return TestClient(app)


# ====================================================================
# M14-2：HOST=0.0.0.0（通配绑定）时，Host 白名单整体失效
# ====================================================================


def test_m14_2_wildcard_host_rejects_arbitrary_host_header(monkeypatch):
    """【已修复 M14-2】通配绑定不再放行任意 Host —— 域名一律拒绝

    修复前：`HOST_IS_WILDCARD=True` 时整段跳过 Host 白名单，`attacker.example.com`
    返回 200。
    修复后：白名单改为「回环 + 本机全部网卡 IP」，域名不在其中 → 403。
    局域网友好性由「本机 IP 在白名单」保证（见下一条断言）。
    """
    monkeypatch.setattr("app.core.middleware.IS_FNOS", False)
    monkeypatch.setattr("app.core.middleware.HOST_IS_WILDCARD", True)
    monkeypatch.setattr("app.core.middleware.ALLOW_HOSTNAME_WHEN_WILDCARD", False)
    monkeypatch.setattr(
        "app.core.middleware.ALLOWED_HOSTS",
        frozenset({"127.0.0.1", "localhost", "::1", "192.168.1.10"}),
    )

    client = make_probe_client()
    # 修复后：攻击者域名被拦
    resp = client.get("/probe", headers={"Host": "attacker.example.com"})
    assert resp.status_code == 403, "M14-2 修复失效：通配绑定下域名仍被放行"
    assert resp.json() == FORBIDDEN_BODY

    # 局域网按 IP 访问不受影响（白名单含本机网卡 IP）
    assert (
        client.get("/probe", headers={"Host": "192.168.1.10:8090"}).status_code == 200
    )

    # 非通配绑定下的同一请求同样被拦（两种形态一致）
    monkeypatch.setattr("app.core.middleware.HOST_IS_WILDCARD", False)
    client2 = make_probe_client()
    blocked = client2.get("/probe", headers={"Host": "attacker.example.com"})
    assert blocked.status_code == 403
    assert blocked.json() == FORBIDDEN_BODY


def test_m14_2_wildcard_still_keeps_origin_check(monkeypatch):
    """【现状确认 M14-2】通配绑定时 Origin 同源校验仍在：跨站写请求仍被拦

    这条不是缺陷，而是缺陷的边界：DNS rebinding 走的是「浏览器同源策略失效 +
    Host 伪造」，攻击页面发出的跨源 XHR 仍带真实 Origin，因此被拦；但只要攻击
    者让受害者直接访问 `http://attacker.example.com:8090/`（页面与伪造 Host
    同源），Origin 校验就不起作用了 —— 这正是 M14-2 的危害路径。
    """
    monkeypatch.setattr("app.core.middleware.IS_FNOS", False)
    monkeypatch.setattr("app.core.middleware.HOST_IS_WILDCARD", True)

    client = make_probe_client()
    resp = client.post(
        "/submit",
        headers={"Host": "attacker.example.com", "Origin": "https://evil.com"},
    )
    assert resp.status_code == 403
    assert resp.json() == FORBIDDEN_BODY


def test_m14_2_same_origin_attack_page_blocked(monkeypatch):
    """【已修复 M14-2 危害路径】攻击页面与伪造 Host 同源 → 也被拦

    模拟 DNS rebinding 的第二步：浏览器地址栏是 attacker.example.com，
    恶意 JS 同源请求本应用。修复前 Host 白名单因通配关闭、Origin 校验因同源通过
    → 数据被读取；修复后 Host 不在白名单 → 403（Origin 校验已不再是唯一防线）。
    """
    monkeypatch.setattr("app.core.middleware.IS_FNOS", False)
    monkeypatch.setattr("app.core.middleware.HOST_IS_WILDCARD", True)
    monkeypatch.setattr("app.core.middleware.ALLOW_HOSTNAME_WHEN_WILDCARD", False)
    monkeypatch.setattr(
        "app.core.middleware.ALLOWED_HOSTS",
        frozenset({"127.0.0.1", "localhost", "::1"}),
    )

    client = make_probe_client()
    resp = client.get(
        "/probe",
        headers={
            "Host": "attacker.example.com",
            "Origin": "http://attacker.example.com",
        },
    )
    assert resp.status_code == 403, "M14-2 修复失效：同源攻击页面仍可读取数据"


def test_m14_2_fnos_mode_also_skips_host_check(monkeypatch):
    """【现状确认】fnOS 网关模式下 Host 校验整体跳过（设计如此，非缺陷）

    网关负责鉴权与来源约束，且应用被桌面 iframe 跨子域嵌入。
    记录此行为是为了划清 M14-2 的适用边界：只有独立部署才需要修。
    """
    monkeypatch.setattr("app.core.middleware.IS_FNOS", True)
    client = make_probe_client()
    assert (
        client.get("/probe", headers={"Host": "attacker.example.com"}).status_code
        == 200
    )


def test_m14_2_production_env_dev_is_wildcard():
    """【环境确认 M14-2】`.env.dev` 把 HOST 配成 0.0.0.0，即本地/默认开发部署
    天然处于白名单关闭状态 —— 缺陷不是理论问题，是默认形态。
    """
    from pathlib import Path

    env_dev = Path(__file__).resolve().parents[2] / ".env.dev"
    if not env_dev.exists():
        # .env.dev 被 .gitignore 排除（本地部署配置不入库），全新克隆/CI 检出
        # 没有这个文件；该环境确认用例只在存在此文件的本地环境执行
        pytest.skip(".env.dev 不入库（gitignore），全新检出环境跳过")
    text = env_dev.read_text(encoding="utf-8", errors="replace")
    host_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip().upper().startswith("HOST=")
    ]
    assert host_lines, ".env.dev 未配置 HOST"
    value = host_lines[0].split("=", 1)[1].strip()
    assert value in ("0.0.0.0", "*", "::"), f"预期 .env.dev 为通配绑定，实际 {value!r}"


# ====================================================================
# M5-2 / M5-3：webhook URL 无内网 / 云元数据地址黑名单（SSRF）
# ====================================================================
#
# 【已修复】修复前 `send_webhook` 只校验 URL 以 http(s):// 开头，回环/私网/
# 云元数据地址一律放行，可探测内网端口与实例凭证。现在出站前走
# `app.utils.net_guard.validate_outbound_url`：解析目标主机全部 IP，
# 拒绝回环/私网/链路本地/保留/组播/未指定地址，解析失败则 fail-closed。
#
# 下面三条用例覆盖真实拦截路径（非复刻逻辑）。

# 应被拒绝的目标：回环、私网、链路本地（含云元数据）、保留地址
_BLOCKED_TARGETS = [
    # --- 回环（M5-2）---
    "http://127.0.0.1:8090/api/settings/database",
    "http://127.0.0.1:1/",
    "http://localhost/admin",
    "http://localhost.localdomain/admin",
    "http://0.0.0.0:22/",
    "http://[::1]:6379/",
    # --- 云元数据（M5-3）---
    "http://169.254.169.254/latest/meta-data/",
    "http://169.254.170.2/v2/credentials/",
    "http://100.100.100.200/latest/meta-data/",
    "http://metadata.google.internal/computeMetadata/v1/",
    # --- 私网（M5-3）---
    "http://192.168.1.1/",
    "http://10.0.0.1:3306/",
    "http://172.16.0.1/",
    "http://[fc00::1]/",
    "http://[fe80::1]/",
]


@pytest.mark.parametrize("url", _BLOCKED_TARGETS)
def test_m5_2_m5_3_webhook_rejects_internal_targets(url):
    """【已修复 M5-2/M5-3】webhook 出站前拒绝回环/私网/云元数据地址

    走**真实实现**（`validate_outbound_url`），不再复刻准入函数 ——
    修复前这些地址全部通过 scheme 校验并被真正发出。
    """
    from app.utils.net_guard import (
        OutboundBlockedError,
        validate_outbound_url,
    )  # noqa: PLC0415

    with pytest.raises(OutboundBlockedError):
        validate_outbound_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://1.1.1.1/",  # 公网字面量（Cloudflare DNS）
        "https://93.184.216.34/v1",  # 公网字面量
        "http://[2606:4700:4700::1111]/",  # 公网 IPv6 字面量
    ],
)
def test_m5_2_public_ip_literals_still_allowed(url):
    """【已修复 M5-2 对照】公网 IP 字面量不受影响，避免修复过度导致功能不可用

    用字面量而非域名：域名需要真实 DNS，单测不应依赖网络。
    域名解析路径由下一条用例用替身覆盖。
    """
    from app.utils.net_guard import validate_outbound_url  # noqa: PLC0415

    assert validate_outbound_url(url) == url


def test_m5_2_public_domain_allowed_when_resolves_to_public(monkeypatch):
    """【已修复 M5-2 对照】解析到公网 IP 的域名正常放行（含 DNS 解析路径）

    替身只替换 `socket.getaddrinfo`（模拟 DNS 应答），`_is_disallowed_ip`
    等判定仍走真实实现 —— 这样既覆盖域名走查 DNS 的分支，又不依赖真实网络。
    """
    import socket as _socket  # noqa: PLC0415

    from app.utils import net_guard  # noqa: PLC0415

    def fake_getaddrinfo(host, port, **kwargs):
        assert host == "api.example.com"
        return [(2, 1, 6, "", ("1.2.3.4", 0))]

    monkeypatch.setattr(_socket, "getaddrinfo", fake_getaddrinfo)
    url = "https://api.example.com/v1"
    assert net_guard.validate_outbound_url(url) == url


def test_m5_2_domain_resolving_to_loopback_rejected(monkeypatch):
    """【已修复 M5-2】公网域名解析到 127.0.0.1 时仍被拒（防 DNS rebinding）

    典型手法：`localtest.me` 这类域名 A 记录指向回环。只看字面量拦不住，
    必须解析后判定 —— 本用例锁定该分支。
    """
    import socket as _socket  # noqa: PLC0415

    from app.utils import net_guard  # noqa: PLC0415

    def fake_getaddrinfo(host, port, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(_socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(net_guard.OutboundBlockedError):
        net_guard.validate_outbound_url("http://localtest.me/")


def test_m5_2_multi_record_any_blocked_rejects_all(monkeypatch):
    """【已修复 M5-2】多 A 记录中任一条落到内网即整体拒绝（防绕过）"""
    import socket as _socket  # noqa: PLC0415

    from app.utils import net_guard  # noqa: PLC0415

    def fake_getaddrinfo(host, port, **kwargs):
        return [
            (2, 1, 6, "", ("1.2.3.4", 0)),  # 公网
            (2, 1, 6, "", ("10.0.0.5", 0)),  # 内网 → 应导致整体拒绝
        ]

    monkeypatch.setattr(_socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(net_guard.OutboundBlockedError):
        net_guard.validate_outbound_url("http://mixed.example.com/")


def test_m5_2_unresolvable_host_rejected(monkeypatch):
    """【已修复 M5-2】解析失败 fail-closed（不放过未验证目标）"""
    import socket as _socket  # noqa: PLC0415

    from app.utils import net_guard  # noqa: PLC0415

    def boom(host, port, **kwargs):
        raise _socket.gaierror("name or service not known")

    monkeypatch.setattr(_socket, "getaddrinfo", boom)
    with pytest.raises(net_guard.OutboundBlockedError) as exc_info:
        net_guard.validate_outbound_url("http://no-such-host.invalid/")
    assert "无法解析" in str(exc_info.value)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_INFO",
        "//evil.com/x",
        "ftp://a/b",
    ],
)
def test_m5_2_non_http_scheme_still_rejected(url):
    """【现状确认】scheme 白名单仍然有效（非 http(s) 被拒）"""
    from app.utils.net_guard import (
        OutboundBlockedError,
        validate_outbound_url,
    )  # noqa: PLC0415

    with pytest.raises(OutboundBlockedError):
        validate_outbound_url(url)


def test_m5_2_webhook_really_blocks_loopback(loopback_http_server, monkeypatch):
    """【已修复 M5-2 实链路】走完整 send_webhook，请求**不会**发到回环服务

    修复前：请求真的打到本机服务（`hits` 非空、返回 ok=True）。
    修复后：出站前拦截，`ok=False` 且 `hits` 为空 —— 证明「可探测」已被切断。
    注意本机代理：删掉代理环境变量，确保「未发出」不是因为被代理拦下而误判。
    """
    base_url, hits = loopback_http_server
    from app.services import notify_service  # noqa: PLC0415

    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        monkeypatch.delenv(name, raising=False)

    ok, error = notify_service.send_webhook("generic", base_url, "标题", "内容")
    assert ok is False, "内网地址竟被放行发出"
    assert not hits, f"出站请求仍打到了本机服务：{hits}"
    assert "不允许" in error or "拒绝" in error, f"失败原因应指向地址校验：{error!r}"


def test_m5_5_no_redirect_followed():
    """【现状确认 M5-5】出站请求不跟随重定向（防 3xx 绕过 scheme 校验）"""
    from app.services.notify_service import _NO_REDIRECT  # noqa: PLC0415

    req = urllib.request.Request("http://127.0.0.1:1/redirect")
    assert (
        _NO_REDIRECT.redirect_request(req, None, 302, "Found", {}, "http://evil.com/")
        is None
    )


# ====================================================================
# M5-7：AI 通道的内网黑名单（风险更高 —— API Key 会随请求外发）
# ====================================================================


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:6379/v1",
        "http://169.254.169.254/latest/meta-data",
        "http://100.100.100.200/latest/meta-data",
        "http://192.168.1.1/v1",
    ],
)
def test_m5_7_ai_base_url_save_guard_rejects_internal(base_url):
    """【已修复 M5-7 第一道】保存 AI 配置时拒绝内网/元数据地址

    走 `app/api/ai.py::_apply_form` 的真实校验体（直接调用 net_guard，
    不依赖数据库会话）。修复前这里只判 scheme 前缀。
    """
    from app.utils.net_guard import (
        OutboundBlockedError,
        validate_outbound_url,
    )  # noqa: PLC0415

    with pytest.raises(OutboundBlockedError):
        validate_outbound_url(base_url)


def test_m5_7_ai_chat_blocks_internal_before_sending(monkeypatch):
    """【已修复 M5-7 第二道·纵深防御】配置被直接篡改时，_chat 出站前仍拦得住

    即使绕过保存接口（手改配置文件 / 残留旧值），`_chat` 在发包前再校验一次，
    确保 API Key 不会以 Bearer 头发往内网地址。
    """
    from app.file_settings import AISettings  # noqa: PLC0415
    from app.services import ai_service  # noqa: PLC0415

    sent = []
    monkeypatch.setattr(
        ai_service, "_open", lambda req, timeout: sent.append(req) or object()
    )
    settings = AISettings(
        api_key="sk-must-not-leak", base_url="http://127.0.0.1:6379", model="m"
    )
    with pytest.raises(ai_service.AIClientError) as exc_info:
        ai_service._chat(settings, [{"role": "user", "content": "hi"}], max_tokens=8)
    assert "不安全" in str(exc_info.value) or "不允许" in str(exc_info.value)
    assert not sent, "危险请求竟被发出（API Key 会随之泄露）"


def test_m5_7_ai_service_source_has_second_line_of_defense():
    """【已修复 M5-7】ai_service 源码确实接入了出站校验（防日后被删）"""
    from app.services import ai_service  # noqa: PLC0415

    with open(ai_service.__file__, encoding="utf-8") as fh:
        text = fh.read()
    assert "validate_outbound_url" in text, "ai_service 丢失出站地址校验"
    assert "OutboundBlockedError" in text, "ai_service 未处理地址拦截异常"
