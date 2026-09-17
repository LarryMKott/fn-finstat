"""更新检查测试：semver 比较、渠道判定、Release 解析、网络失败降级与接口契约

外部 HTTP 一律经 monkeypatch 替换 update_service._fetch_releases，单测不发起
真实网络请求（与 test_ai_service 同策略）。Release 记录按 Gitee API 真实响应
的字段构造，避免用简化结构测出与线上不一致的结论。
"""

import urllib.error

import pytest

from app.schemas.update import UpdateCheckResult
from app.services import update_service
from tests.conftest import USER_A, USER_B

A_HEADERS = {
    "X-Trim-Userid": USER_A,
    "X-Trim-Username": "zhangsan",
    "X-Trim-Isadmin": "true",
}
B_HEADERS = {"X-Trim-Userid": USER_B}

DOWNLOAD_PREFIX = "https://gitee.com/zhangyilin_233/fn-finstat/releases/download"


@pytest.fixture(autouse=True)
def clear_update_cache():
    """结果缓存是进程级全局，必须逐用例清空，否则用例间互相串味"""
    update_service.clear_cache()
    yield
    update_service.clear_cache()


def make_release(
    tag: str,
    *,
    prerelease: bool = False,
    assets: tuple = (),
    body: str = "",
    name: str = "",
    created_at: str = "2026-09-17T14:54:26+08:00",
) -> dict:
    """构造一条与 Gitee Release 接口同构的记录"""
    return {
        "tag_name": tag,
        "name": name or f"fn-finstat {tag}",
        "prerelease": prerelease,
        "created_at": created_at,
        "body": body,
        "assets": [
            {
                "name": fname,
                "browser_download_url": f"{DOWNLOAD_PREFIX}/{tag}/{fname}",
            }
            for fname in assets
        ],
    }


def fake_fetch(releases, counter: list | None = None):
    """返回一个可替换 _fetch_releases 的假实现（counter 用于断言调用次数）"""

    def _fetch(timeout: float = 0):
        if counter is not None:
            counter.append(timeout)
        return releases

    return _fetch


# ---- 版本号比较（semver 2.0）----


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("0.7.1", "0.7.1", 0),
        ("0.7.2", "0.7.1", 1),
        ("0.7.0", "0.7.1", -1),
        ("2.0.0", "10.0.0", -1),  # 数字段按数值比，不能按字符串
        ("1.0.0", "1.0.0+build.9", 0),  # 构建元数据不参与比较
        # 预发布版本小于同号正式版：算反会让正式版用户被反复提示升级
        ("0.7.1", "0.7.1-dev.2.g8c2979a", 1),
        ("0.7.1-dev.2.g8c2979a", "0.7.1", -1),
        # 预发布段内的数字段同样按数值比（dev.10 > dev.2）
        ("0.7.1-dev.10.g0", "0.7.1-dev.2.g0", 1),
        ("0.7.2-dev.3.g6e5ffc1", "0.7.1-dev.2.g8c2979a", 1),
        # 标识符个数多者更大
        ("0.7.1-dev", "0.7.1-dev.2", -1),
        ("1.0.0-alpha", "1.0.0-alpha.1", -1),
        # 数字标识符 < 字母标识符
        ("1.0.0-1", "1.0.0-alpha", -1),
        ("1.0.0-alpha.beta", "1.0.0-beta", -1),
    ],
)
def test_compare_versions_matrix(left, right, expected):
    assert update_service.compare_versions(left, right) == expected


@pytest.mark.parametrize("text", ["", "abc", "v", "v23", "v0.7", "0.7.1.2", "1.x.0"])
def test_compare_versions_rejects_non_semver(text):
    """非三段式版本号一律返回 None（不可比较 ≠ 相同）"""
    assert update_service.compare_versions(text, "0.7.1") is None
    assert update_service.compare_versions("0.7.1", text) is None
    assert update_service.normalize_version(text) == ""


def test_normalize_version_strips_v_prefix():
    assert update_service.normalize_version("v0.7.1") == "0.7.1"
    assert update_service.normalize_version(" 0.7.1-dev.2 ") == "0.7.1-dev.2"


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.7.1", update_service.CHANNEL_RELEASE),
        ("0.7.1-dev.2.g8c2979a", update_service.CHANNEL_DEV),
        ("0.8.0-rc.1", update_service.CHANNEL_DEV),
        ("garbage", update_service.CHANNEL_RELEASE),  # 解析不了时按正式渠道兜底
    ],
)
def test_channel_of(version, expected):
    assert update_service.channel_of(version) == expected


# ---- Release 解析 ----


def test_parse_release_prefers_versioned_asset():
    """下载直链优先带版本号副本：它最能回答「下到的是哪一次构建」"""
    raw = make_release(
        "v0.7.1",
        assets=(
            "fn-finstat.fpk",
            "fn-finstat-latest.fpk",
            "fn-finstat-v0.7.1.fpk",
            "releaseNode.txt",
        ),
    )
    release = update_service.parse_release(raw)
    assert release.download_url.endswith("/v0.7.1/fn-finstat-v0.7.1.fpk")
    assert release.checksum_url == ""
    assert release.page_url == f"{update_service.RELEASES_PAGE_URL}/tag/v0.7.1"


def test_parse_release_falls_back_past_missing_versioned_copy():
    """带版本号副本缺失时依次退回裸名 → 任意 fpk，不给空链接"""
    bare = update_service.parse_release(
        make_release("v0.7.1", assets=("fn-finstat.fpk", "fn-finstat-latest.fpk"))
    )
    assert bare.download_url.endswith("/v0.7.1/fn-finstat.fpk")
    # 连裸名都没有（异常打包）时仍给出同一 Release 下的任意 fpk
    any_fpk = update_service.parse_release(
        make_release("v0.7.1", assets=("releaseNode.txt", "fn-finstat-dev.fpk"))
    )
    assert any_fpk.download_url.endswith("/v0.7.1/fn-finstat-dev.fpk")
    # 只有校验文件、没有 fpk 时留空（界面据此隐藏下载入口）
    none = update_service.parse_release(make_release("v0.7.0", assets=("MD5SUMS.txt",)))
    assert none.download_url == ""
    assert none.checksum_url.endswith("/v0.7.0/MD5SUMS.txt")


@pytest.mark.parametrize("tag", ["v", "v23", "v33", "nightly"])
def test_parse_release_drops_legacy_tags(tag):
    """历史构建号 tag 必须剔除：`v23` 会被当成 23.0.0 而永远「最新」"""
    assert update_service.parse_release(make_release(tag)) is None
    assert update_service.parse_release("not-a-dict") is None


CHANGELOG_BODY = """# 更新日志

本文件由 `scripts/gen_release_notes.py` 自动生成。

## fn-finstat v0.7.1

> 📅 发布日期：2026-09-17

### ✨ 新功能

- **app**: 新增检查更新 (abc1234)

## fn-finstat v0.7.0

### 🐛 问题修复

- **old**: 旧版本内容 (def5678)
"""


def test_parse_release_notes_only_keeps_current_version():
    """Release 描述取自整篇 CHANGELOG.md，只应截出最新一节"""
    notes = update_service.parse_release(
        make_release("v0.7.1", body=CHANGELOG_BODY)
    ).notes
    assert notes.startswith("## fn-finstat v0.7.1")
    assert "新增检查更新" in notes
    assert "旧版本内容" not in notes


def test_parse_release_notes_truncated():
    """超长正文按上限摘录（界面只做概要展示）"""
    body = "## fn-finstat v0.7.1\n\n" + "变更说明。" * 300
    notes = update_service.parse_release(make_release("v0.7.1", body=body)).notes
    assert len(notes) == update_service.NOTES_MAX_LENGTH + 1
    assert notes.endswith("…")


# ---- 检查流程：渠道过滤与版本关系 ----


def test_check_release_channel_ignores_prerelease(monkeypatch):
    """正式渠道只看正式 Release：绝不能把测试包推给正式版用户"""
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch(
            [
                make_release("v0.7.2-dev.3.g6e5ffc1", prerelease=True),
                make_release("v0.7.1", assets=("fn-finstat-v0.7.1.fpk",)),
            ]
        ),
    )
    result = update_service.check_for_update()
    assert isinstance(result, UpdateCheckResult)
    assert result.ok is True
    assert result.channel == update_service.CHANNEL_RELEASE
    assert result.latest_version == "0.7.1"
    assert result.relation == "same"
    assert result.message == "已是最新版本"


def test_check_release_channel_reports_newer(monkeypatch):
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch(
            [
                make_release("v0.8.0", assets=("fn-finstat-latest.fpk",)),
                make_release("v0.7.1", assets=("fn-finstat-latest.fpk",)),
            ]
        ),
    )
    result = update_service.check_for_update()
    assert result.relation == "newer"
    assert result.latest_version == "0.8.0"
    assert result.download_url.endswith("/v0.8.0/fn-finstat-latest.fpk")
    assert "0.8.0" in result.message


def test_check_dev_channel_sees_prerelease_and_stable(monkeypatch):
    """测试渠道与全部 Release 比较：既能看到更新的测试包，也能看到正式版"""
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1-dev.2.g8c2979a")
    newest_dev = make_release("v0.7.2-dev.3.g6e5ffc1", prerelease=True)
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch([newest_dev, make_release("v0.7.1"), make_release("v23")]),
    )
    result = update_service.check_for_update()
    assert result.channel == update_service.CHANNEL_DEV
    assert result.latest_version == "0.7.2-dev.3.g6e5ffc1"
    assert result.relation == "newer"

    # 正式版超过本地测试版时，测试用户也应收到提示
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch([make_release("v0.8.0"), newest_dev]),
    )
    assert update_service.check_for_update(refresh=True).latest_version == "0.8.0"


def test_check_reports_older_for_unreleased_build(monkeypatch):
    """本机是未发布构建（比已发布版本更新）时给出 older 而不是「有新版本」"""
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.9")
    monkeypatch.setattr(
        update_service, "_fetch_releases", fake_fetch([make_release("v0.7.1")])
    )
    result = update_service.check_for_update()
    assert result.relation == "older"
    assert "未发布构建" in result.message


def test_check_without_usable_release_degrades(monkeypatch):
    """远端只有历史 tag 时降级为失败结果，而不是拿 v23 当最新版"""
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch([make_release("v23"), make_release("v")]),
    )
    result = update_service.check_for_update()
    assert result.ok is False
    assert result.message == "更新服务器上没有可用的版本记录"


# ---- 离线 / 异常降级：必须返回 ok=False 而不是抛错 ----


def raising_fetch(exc: Exception):
    def _fetch(timeout: float = 0):
        raise exc

    return _fetch


def test_check_offline_degrades(monkeypatch):
    """离线部署是常态：连不上更新服务器不能变成 500，也不能打断设置页"""
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        raising_fetch(urllib.error.URLError("nodename")),
    )
    result = update_service.check_for_update()
    assert result.ok is False
    assert "无法连接更新服务器" in result.message
    assert result.latest_version == ""
    assert result.checked_at


def test_check_timeout_and_http_error_degrade(monkeypatch):
    monkeypatch.setattr(
        update_service, "_fetch_releases", raising_fetch(TimeoutError())
    )
    assert update_service.check_for_update().message == "连接更新服务器超时（>10s）"

    update_service.clear_cache()
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        raising_fetch(urllib.error.HTTPError("u", 403, "Forbidden", {}, None)),
    )
    assert "403" in update_service.check_for_update().message


def test_check_bad_payload_degrades(monkeypatch):
    """接口返回非列表（被网关拦截返回 HTML/JSON 对象）时给可读提示"""
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        raising_fetch(ValueError("接口未返回 Release 列表")),
    )
    assert "响应异常" in update_service.check_for_update().message


# ---- 结果缓存 ----


def test_result_cached_until_refresh(monkeypatch):
    calls: list = []
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch([make_release("v0.7.1")], counter=calls),
    )
    assert update_service.check_for_update().cached is False
    assert update_service.check_for_update().cached is True
    assert update_service.check_for_update(refresh=True).cached is False
    assert len(calls) == 2


def test_failure_is_cached_too(monkeypatch):
    """失败结果同样缓存：离线时每次进设置页都重试会白等一个超时"""
    calls: list = []

    def _fetch(timeout: float = 0):
        calls.append(1)
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(update_service, "_fetch_releases", _fetch)
    first = update_service.check_for_update()
    second = update_service.check_for_update()
    assert first.ok is False and second.ok is False and second.cached is True
    assert len(calls) == 1


# ---- 接口契约 ----


def test_api_check_update_contract(client, monkeypatch):
    """普通账号可查（只读远端公开信息），统一响应体 + 渠道与版本字段齐全"""
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch(
            [make_release("v0.8.0", assets=("fn-finstat-latest.fpk", "MD5SUMS.txt"))]
        ),
    )
    resp = client.get("/api/update/check", headers=B_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0 and body["msg"] == "ok"
    data = body["data"]
    assert data["ok"] is True
    assert data["current_version"] == "0.7.1"
    assert data["latest_version"] == "0.8.0"
    assert data["relation"] == "newer"
    assert data["download_url"].endswith("/v0.8.0/fn-finstat-latest.fpk")
    assert data["checksum_url"].endswith("/v0.8.0/MD5SUMS.txt")
    assert data["cached"] is False


def test_api_check_update_accepts_refresh(client, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch([make_release("v0.7.1")], counter=calls),
    )
    assert (
        client.get("/api/update/check", headers=A_HEADERS).json()["data"]["cached"]
        is False
    )
    assert (
        client.get("/api/update/check", headers=A_HEADERS).json()["data"]["cached"]
        is True
    )
    forced = client.get("/api/update/check?refresh=true", headers=A_HEADERS)
    assert forced.json()["data"]["cached"] is False
    assert len(calls) == 2


def test_api_check_update_offline_is_200(client, monkeypatch):
    """离线时接口仍返回 200（ok=false 表达失败），避免前端把常态当异常弹错"""
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        raising_fetch(urllib.error.URLError("offline")),
    )
    resp = client.get("/api/update/check", headers=B_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["ok"] is False
