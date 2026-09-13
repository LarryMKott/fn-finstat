"""运行日志接口测试：设置页日志尾部查看与下载、导入过程日志写入"""

import logging
from io import BytesIO

from tests.conftest import USER_A
from tests.test_ai_service import wechat_rows

A_HEADERS = {
    "X-Trim-Userid": USER_A,
    "X-Trim-Username": "zhangsan",
    "X-Trim-Isadmin": "true",
}


# ---------- 日志查看 / 下载 ----------


def test_settings_logs_tail(client, tmp_path):
    log_file = tmp_path / "app.log"
    log_file.write_text(
        "\n".join(f"line-{i:04d}" for i in range(500)) + "\n", encoding="utf-8"
    )

    resp = client.get("/api/settings/logs", params={"lines": 100}, headers=A_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["lines"] == 100
    assert data["truncated"] is True
    assert data["path"] == str(log_file)
    assert data["size"] == log_file.stat().st_size
    content_lines = data["content"].splitlines()
    assert len(content_lines) == 100
    assert content_lines[0] == "line-0400"  # 末尾 100 行从 0400 开始
    assert content_lines[-1] == "line-0499"


def test_settings_logs_small_file_no_truncation(client, tmp_path):
    log_file = tmp_path / "app.log"
    log_file.write_text("a\nb\nc\n", encoding="utf-8")
    resp = client.get("/api/settings/logs", params={"lines": 100}, headers=A_HEADERS)
    data = resp.json()
    assert data["truncated"] is False
    assert data["content"].splitlines() == ["a", "b", "c"]


def test_settings_logs_missing_file(client, tmp_path):
    # 临时目录下不创建日志文件，接口应返回空内容而不是报错
    resp = client.get("/api/settings/logs", headers=A_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"] == ""
    assert data["size"] == 0
    assert data["truncated"] is False


def test_settings_logs_invalid_lines_param(client):
    resp = client.get("/api/settings/logs", params={"lines": 5}, headers=A_HEADERS)
    assert resp.status_code == 422  # 路由层 ge=10 校验


def test_settings_logs_download(client, tmp_path):
    log_file = tmp_path / "app.log"
    log_file.write_text("hello 运行日志\n", encoding="utf-8")
    resp = client.get("/api/settings/logs/download", headers=A_HEADERS)
    assert resp.status_code == 200
    assert "hello 运行日志" in resp.text
    assert "text/plain" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    # 快照式下载：声明长度必须与实际字节一致（流式发送会因日志持续追加而错位）
    assert int(resp.headers["content-length"]) == len(resp.content)


def test_settings_logs_download_missing(client, tmp_path):
    resp = client.get("/api/settings/logs/download", headers=A_HEADERS)
    assert resp.status_code == 404


# ---------- 导入过程日志 ----------


def test_import_writes_process_logs(client, tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="app.services.import_service"):
        resp = client.post(
            "/api/upload/wechat",
            files={
                "file": (
                    "bill.xlsx",
                    wechat_rows("瑞幸咖啡", "滴滴出行"),
                    "application/octet-stream",
                )
            },
            headers=A_HEADERS,
        )
    assert resp.status_code == 200
    messages = [r.getMessage() for r in caplog.records]
    assert any("开始导入" in m and "bill.xlsx" in m for m in messages)
    assert any(
        "账单导入完成" in m and "解析 2 条" in m and "新增 2 条" in m for m in messages
    )


def test_import_failure_logs_reason(client, tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.import_service"):
        resp = client.post(
            "/api/upload/wechat",
            files={
                "file": (
                    "bad.xlsx",
                    BytesIO(b"not an excel file"),
                    "application/octet-stream",
                )
            },
            headers=A_HEADERS,
        )
    assert resp.status_code == 400
    assert any("账单导入失败" in r.getMessage() for r in caplog.records)
