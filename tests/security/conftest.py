"""OWASP 专项安全测试的公共夹具

与 tests/conftest.py 的分工：
- 根 conftest 提供 db / client / ai_config_isolated 等业务夹具
- 本文件只补充「网络出站」与「带完整中间件栈的端到端应用」两类夹具，
  供 SSRF / 来源校验 / 敏感信息泄露等用例使用
"""

import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture()
def loopback_http_server():
    """本机回环 HTTP 服务：记录收到的请求，用于验证出站请求真实发出（SSRF 探测）

    返回 (base_url, hits)，hits 为收到的路径列表。用例结束自动关停。
    """
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            hits.append(f"POST {self.path} {body[:200]!r}")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *args):  # 静音
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", hits
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def free_port() -> int:
    """获取一个当前空闲的 TCP 端口号（用于「端口有服务/无服务」对比探测）"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_python(code: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """在子进程中执行一段 Python（用于需要干净模块状态的探测）"""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
