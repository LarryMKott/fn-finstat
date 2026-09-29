"""30x 重定向禁用回归（安全审计修复）：三处出站通道都不得跟随跳转

历史缺陷：_NoRedirect 只继承未重写 redirect_request（notify_service 甚至只是
直接实例化基类），urllib 仍按默认策略跟随 3xx——AI 通道的
「Authorization: Bearer <Key>」会被原样发往跳转目标（实测泄漏），Webhook
可借跳转把出站请求引向内网。

本文件起一个「任何请求一律 302 → 指向必然拒绝连接端口」的本地 HTTP 服务器，
验证 302 被当作 HTTPError 拒绝跟随（跟随的话会变成 URLError 连接拒绝），
而不是只测类签名。
"""

import http.server
import threading
import urllib.request

import pytest

from app.file_settings import AISettings
from app.services import ai_service, notify_service, update_service

# 本文件全部用例的目标都是 127.0.0.1 临时服务器（回环），而 SSRF 防线会拦截回环
# → 整模块放行地址校验（见 bypass_env_proxy docstring）
pytestmark = pytest.mark.usefixtures("outbound_guard_bypass")


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    """所有 GET/POST 一律 302，Location 指向 127.0.0.1:1（保留端口，必拒绝）"""

    def _redirect(self):
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:1/never")
        self.end_headers()

    do_GET = _redirect
    do_POST = _redirect

    def log_message(self, *args):  # 测试静默
        pass


@pytest.fixture(autouse=True)
def bypass_env_proxy(monkeypatch):
    """本文件起的是本地服务器，请求必须直连：就地取消代理并重建模块级 opener

    环境注入 HTTP_PROXY 时本文件会报假失败（`更新接口返回 502` 断言 `302`）。
    实测两个必要条件，缺一不可：

    1. **删代理环境变量**——否则发出的请求走代理，拿不到本地服务器的 302。
       注意只有「URL 带 query」的请求会被代理拦下（裸 URL 反而直连），
       所以历史上表现为「同一个文件里只有 update 那条红」。
    2. **重建 `_OPENER`**——`ai_service` / `update_service` 的 `_OPENER` 是
       **模块级常量**（`build_opener(_NoRedirect)`），ProxyHandler 在 import
       时就把代理配置固化了，测试期再删 env 对它无效，必须重新构建。

    另：本文件的验证手段是「`127.0.0.1` 临时服务器 + 302」，而 SSRF 防线
    （`net_guard.validate_outbound_url`）恰好禁止回环目标 → 本文件所有用例
    均标 `usefixtures("outbound_guard_bypass")` 有意放行地址校验，专注于
    「是否跟随重定向」这一件事。防线的有效性由 `tests/security/` 覆盖。

    只在本文件生效，不全局改，也不改产品代码行为。
    详见 `docs/devlog/2026-09-14-工程踩坑与硬约束备忘.md`。
    """
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    for module in (ai_service, update_service):
        monkeypatch.setattr(
            module, "_OPENER", urllib.request.build_opener(module._NoRedirect)
        )


@pytest.fixture()
def redirect_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize(
    "handler_cls",
    [ai_service._NoRedirect, update_service._NoRedirect, notify_service._NoRedirect],
)
def test_redirect_request_returns_none(handler_cls):
    """三处 handler 都必须显式重写 redirect_request 并返回 None（禁跟随契约）"""
    assert handler_cls().redirect_request(None, None, 302, "x", {}, "http://x") is None


def test_ai_chat_does_not_follow_redirect(redirect_server):
    """AI 通道：302 以 HTTPError 抛出并转 AIClientError，密钥不随跳转外发"""
    settings = AISettings(api_key="sk-test", base_url=redirect_server)
    with pytest.raises(ai_service.AIClientError) as exc_info:
        ai_service._chat(settings, [{"role": "user", "content": "hi"}], max_tokens=8)
    assert "302" in str(exc_info.value)


def test_update_check_does_not_follow_redirect(redirect_server, monkeypatch):
    """更新检查：302 走既有 HTTPError 降级分支，不取到跳转目标的内容"""
    # 默认发布源 = github，替换其 Release 接口地址为本地跳转服务器
    monkeypatch.setitem(
        update_service._RELEASE_SOURCES[update_service.SOURCE_GITHUB],
        "api_url",
        redirect_server,
    )
    update_service.clear_cache()
    try:
        result = update_service.check_for_update(refresh=True)
    finally:
        update_service.clear_cache()
    assert result.ok is False
    assert "302" in result.message


def test_send_webhook_does_not_follow_redirect(redirect_server):
    """Webhook 出站：302 不被跟随，失败原因为可读的 HTTP 302"""
    ok, error = notify_service.send_webhook(
        "generic", f"{redirect_server}/hook", "标题", "内容"
    )
    assert ok is False
    assert error == "HTTP 302"
