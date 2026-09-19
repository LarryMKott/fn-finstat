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

import pytest

from app.file_settings import AISettings
from app.services import ai_service, notify_service, update_service


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
    monkeypatch.setattr(update_service, "RELEASES_API_URL", redirect_server)
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
