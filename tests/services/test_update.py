"""更新检查测试：semver 比较、渠道判定、Release 解析、网络失败降级与接口契约
（含「下载安装包到 NAS」：流式落盘、MD5 校验、失败降级）

外部 HTTP 一律经 monkeypatch 替换 update_service._fetch_releases / _open_download /
_fetch_bytes，单测不发起真实网络请求（与 test_ai_service 同策略）。Release 记录按
Gitee/GitHub Release API 的同构字段构造，避免用简化结构测出与线上不一致的结论。
"""

import hashlib
import io
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app.file_settings import UpdateSettings
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
    """返回一个可替换 _fetch_releases 的假实现（counter 用于断言调用次数与站点）"""

    def _fetch(source: str, timeout: float = 0):
        if counter is not None:
            counter.append((source, timeout))
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
    release = update_service.parse_release(raw, update_service.SOURCE_GITEE)
    assert release.download_url.endswith("/v0.7.1/fn-finstat-v0.7.1.fpk")
    assert release.checksum_url == ""
    assert release.page_url == (
        f"{update_service._RELEASE_SOURCES[update_service.SOURCE_GITEE]['page_url']}/tag/v0.7.1"
    )


def test_parse_release_page_url_follows_source():
    """详情页地址随站点分派：gitee 与 github 各自指向自己的仓库页面"""
    raw = make_release("v0.7.1")
    gitee = update_service.parse_release(raw, update_service.SOURCE_GITEE)
    github = update_service.parse_release(raw, update_service.SOURCE_GITHUB)
    assert gitee.page_url.startswith("https://gitee.com/")
    assert gitee.page_url.endswith("/releases/tag/v0.7.1")
    assert github.page_url.startswith("https://github.com/")
    assert github.page_url.endswith("/releases/tag/v0.7.1")
    # 缺省站点 = 默认源（github）
    assert update_service.parse_release(raw).page_url == github.page_url


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


def test_parse_release_notes_matches_base_version_for_dev_build():
    """测试包与其基线正式版共用同一份 CHANGELOG 小节（设计如此）"""
    notes = update_service.parse_release(
        make_release(
            "v0.7.3-dev.5.g4cc0a6f", body=CHANGELOG_BODY.replace("0.7.1", "0.7.3")
        )
    ).notes
    assert notes.startswith("## fn-finstat v0.7.3")


def test_parse_release_notes_hidden_when_changelog_stale():
    """CHANGELOG 落后于构建时**宁可没有说明，也不能错标**

    线上实测到的真实缺陷：`.workflow` 用 `description: CHANGELOG.md`，而 CHANGELOG
    只在发版时更新；dev 渠道每次 push 都发 Release，于是新构建的描述里第一节是
    「上一个已发布版本」的日志。原实现无脑取第一段，会把 v0.7.1 的日志当成
    v0.7.3-dev.5 的更新说明展示给用户。
    """
    stale = make_release("v0.7.3-dev.5.g4cc0a6f", body=CHANGELOG_BODY)
    assert update_service.parse_release(stale).notes == ""
    # 版本号不可解析时不再挑剔，退回第一段（好过什么都不给）
    assert update_service._release_notes(CHANGELOG_BODY, "") != ""


def test_parse_release_notes_no_partial_version_confusion():
    """`0.7.1` 不得命中 `v0.7.10` 的小节（边界断言）"""
    body = "## fn-finstat v0.7.10\n\n- **n**: 更高版本 (aaa)\n"
    assert update_service.parse_release(make_release("v0.7.1", body=body)).notes == ""
    both = body + "\n## fn-finstat v0.7.1\n\n- **y**: 本次 (bbb)\n"
    notes = update_service.parse_release(make_release("v0.7.1", body=both)).notes
    assert notes.startswith("## fn-finstat v0.7.1")


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


# ---- 版本线与发布站点选择 ----


def test_check_defaults_follow_current_version_on_github(monkeypatch):
    """默认语义：版本线跟随本机版本（dev 构建看测试线），站点默认 github"""
    calls: list = []
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1-dev.2.g8c2979a")
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch(
            [
                make_release("v0.7.1"),
                make_release("v0.7.2-dev.1.g0", prerelease=True),
            ],
            counter=calls,
        ),
    )
    result = update_service.check_for_update()
    assert result.source == update_service.SOURCE_GITHUB
    assert result.channel == update_service.CHANNEL_DEV
    assert result.latest_version == "0.7.2-dev.1.g0"
    assert result.relation == "newer"
    assert calls == [("github", update_service.REQUEST_TIMEOUT)]


def test_check_explicit_channel_overrides_local_version(monkeypatch):
    """显式版本线优先于本机版本推导：正式包也能看开发线，反之亦然"""
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch(
            [
                make_release("v0.7.2-dev.3.g6e5ffc1", prerelease=True),
                make_release("v0.7.1"),
            ]
        ),
    )
    # 本机是正式包，显式切到开发线 → 测试包被当作「有新版本」
    result = update_service.check_for_update(channel=update_service.CHANNEL_DEV)
    assert result.channel == update_service.CHANNEL_DEV
    assert result.latest_version == "0.7.2-dev.3.g6e5ffc1"
    assert result.relation == "newer"

    # 反向：本机是开发包，显式切回正式线 → 只比正式 Release
    update_service.clear_cache()
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.2-dev.3.g6e5ffc1")
    result = update_service.check_for_update(channel=update_service.CHANNEL_RELEASE)
    assert result.channel == update_service.CHANNEL_RELEASE
    assert result.latest_version == "0.7.1"
    assert result.relation == "older"


def test_check_source_switch_targets_gitee(monkeypatch):
    """source=gitee 走 Gitee 源：结果带站点标识，直链与详情页都指向 Gitee"""
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch([make_release("v0.8.0", assets=("fn-finstat-v0.8.0.fpk",))]),
    )
    result = update_service.check_for_update(source=update_service.SOURCE_GITEE)
    assert result.source == update_service.SOURCE_GITEE
    assert result.relation == "newer"
    assert result.download_url.endswith("/v0.8.0/fn-finstat-v0.8.0.fpk")
    assert result.page_url.startswith("https://gitee.com/")


def test_check_unknown_source_degrades(monkeypatch):
    """未知站点不抛错，给可读失败结果（接口层已用 Literal 校验，这里兜底服务层）"""
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service, "_fetch_releases", fake_fetch([make_release("v0.8.0")])
    )
    result = update_service.check_for_update(source="bitbucket")
    assert result.ok is False
    assert "未知的发布站点" in result.message


def test_cache_is_keyed_by_source_and_channel(monkeypatch):
    """站点与版本线组合各自缓存：切换选择必须重新检查，切回复用、互不挤掉"""
    calls: list = []
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch([make_release("v0.7.1")], counter=calls),
    )
    assert update_service.check_for_update().cached is False
    assert update_service.check_for_update().cached is True  # 同键复用
    assert (
        update_service.check_for_update(channel=update_service.CHANNEL_DEV).cached
        is False
    )
    assert (
        update_service.check_for_update(source=update_service.SOURCE_GITEE).cached
        is False
    )
    # 切回最初的键：复用第一次的结果，不再发请求
    assert update_service.check_for_update().cached is True
    assert len(calls) == 3


# ---- 离线 / 异常降级：必须返回 ok=False 而不是抛错 ----


def raising_fetch(exc: Exception):
    def _fetch(source: str, timeout: float = 0):
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

    def _fetch(source: str, timeout: float = 0):
        calls.append(1)
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(update_service, "_fetch_releases", _fetch)
    first = update_service.check_for_update()
    second = update_service.check_for_update()
    assert first.ok is False and second.ok is False and second.cached is True
    assert len(calls) == 1


# ---- 接口契约 ----


def test_api_check_update_contract(client, monkeypatch):
    """普通账号可查（只读远端公开信息），统一响应体 + 站点/渠道与版本字段齐全"""
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
    assert data["source"] == "github"  # 缺省发布源 = github
    assert data["latest_version"] == "0.8.0"
    assert data["relation"] == "newer"
    assert data["download_url"].endswith("/v0.8.0/fn-finstat-latest.fpk")
    assert data["checksum_url"].endswith("/v0.8.0/MD5SUMS.txt")
    assert data["cached"] is False


def test_api_check_update_source_and_channel_params(client, monkeypatch):
    """接口透传 source / channel：响应带站点标识，请求打到对应源"""
    calls: list = []
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch([make_release("v0.7.2-dev.1.g0", prerelease=True)], counter=calls),
    )
    resp = client.get("/api/update/check?source=gitee&channel=dev", headers=B_HEADERS)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["source"] == "gitee"
    assert data["channel"] == "dev"
    assert data["latest_version"] == "0.7.2-dev.1.g0"
    assert calls[0][0] == "gitee"


def test_api_check_update_rejects_bad_source_or_channel(client):
    """source / channel 越界的请求在参数校验层被拒（422），不会打到远端"""
    assert (
        client.get("/api/update/check?source=bitbucket", headers=B_HEADERS).status_code
        == 422
    )
    assert (
        client.get("/api/update/check?channel=stable", headers=B_HEADERS).status_code
        == 422
    )


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


def test_refresh_is_throttled_within_interval(monkeypatch):
    """REFRESH_MIN_INTERVAL 内的重复强制刷新回缓存结果，不再直连远端

    refresh 接口对所有登录账号开放，节流防止高频点击耗光远端匿名接口额度；
    clear_cache 会一并重置节流时间戳（夹具逐用例清理，用例间不串味）。
    """
    calls: list = []
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch([make_release("v0.7.1")], counter=calls),
    )
    first = update_service.check_for_update(refresh=True)
    second = update_service.check_for_update(refresh=True)
    assert first.cached is False
    assert second.cached is True
    assert len(calls) == 1


# ---- 下载安装包到 NAS ----


def fake_download(payload: bytes):
    """返回可替换 _open_download 的假实现：从内存字节流「下载」"""

    def _open(url: str, timeout: float = 0):
        return io.BytesIO(payload)

    return _open


def fake_checksum(md5: str, file_name: str):
    """返回可替换 _fetch_bytes 的假实现：生成 MD5SUMS.txt 内容"""

    def _fetch(url: str, timeout: float = 0):
        return f"{md5}  {file_name}\n".encode()

    return _fetch


RELEASE_WITH_FPK = lambda: [  # noqa: E731
    make_release(
        "v0.8.0",
        assets=(
            "fn-finstat-latest.fpk",
            "fn-finstat-v0.8.0.fpk",
            "MD5SUMS.txt",
            "releaseNode.txt",
        ),
    )
]


def test_download_writes_versioned_package_and_verifies(monkeypatch, tmp_path):
    """正常下载：优先带版本号副本落盘，MD5 校验通过，无 .part 残件"""
    payload = b"fpk-package-bytes"
    md5 = hashlib.md5(payload).hexdigest()
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service, "_fetch_releases", fake_fetch(RELEASE_WITH_FPK())
    )
    monkeypatch.setattr(update_service, "_open_download", fake_download(payload))
    monkeypatch.setattr(
        update_service, "_fetch_bytes", fake_checksum(md5, "fn-finstat-v0.8.0.fpk")
    )

    result = update_service.download_latest_release(str(tmp_path))
    assert result.ok is True
    assert result.version == "0.8.0"
    assert result.file_name == "fn-finstat-v0.8.0.fpk"  # 带唯一标识的副本优先
    assert result.md5_verified is True
    assert result.size == len(payload)
    assert Path(result.saved_path) == tmp_path / "fn-finstat-v0.8.0.fpk"
    assert Path(result.saved_path).read_bytes() == payload
    assert not list(tmp_path.glob("*.part"))  # 原子改名后不留残件


def test_download_rejects_checksum_mismatch(monkeypatch, tmp_path):
    """MD5 对不上 = 传输残缺：丢弃落盘文件并报失败，绝不交付坏包"""
    payload = b"corrupted-or-truncated"
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service, "_fetch_releases", fake_fetch(RELEASE_WITH_FPK())
    )
    monkeypatch.setattr(update_service, "_open_download", fake_download(payload))
    monkeypatch.setattr(
        update_service,
        "_fetch_bytes",
        fake_checksum("0" * 32, "fn-finstat-v0.8.0.fpk"),
    )
    result = update_service.download_latest_release(str(tmp_path))
    assert result.ok is False
    assert "MD5 校验不匹配" in result.message
    assert list(tmp_path.iterdir()) == []  # 残件已清理


def test_download_without_checksum_asset_succeeds_unverified(monkeypatch, tmp_path):
    """Release 未附校验文件：照常交付，但如实标记未校验"""
    payload = b"pkg"
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch([make_release("v0.8.0", assets=("fn-finstat-v0.8.0.fpk",))]),
    )
    monkeypatch.setattr(update_service, "_open_download", fake_download(payload))
    result = update_service.download_latest_release(str(tmp_path))
    assert result.ok is True
    assert result.md5_verified is False
    assert "未附" in result.message


def test_download_checksum_fetch_failure_keeps_package(monkeypatch, tmp_path):
    """校验文件拉取失败 ≠ 校验不匹配：包本身可能完好，知情交付不删除"""
    payload = b"pkg"
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service, "_fetch_releases", fake_fetch(RELEASE_WITH_FPK())
    )
    monkeypatch.setattr(update_service, "_open_download", fake_download(payload))

    def _broken_fetch(url: str, timeout: float = 0):
        raise urllib.error.URLError("checksum unreachable")

    monkeypatch.setattr(update_service, "_fetch_bytes", _broken_fetch)
    result = update_service.download_latest_release(str(tmp_path))
    assert result.ok is True
    assert result.md5_verified is False
    assert Path(result.saved_path).read_bytes() == payload


def test_download_oversize_aborts(monkeypatch, tmp_path):
    """超过大小上限的异常响应及时止损：中止下载并清理临时文件"""
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service, "_fetch_releases", fake_fetch(RELEASE_WITH_FPK())
    )
    monkeypatch.setattr(update_service, "_open_download", fake_download(b"x" * 1024))
    monkeypatch.setattr(update_service, "DOWNLOAD_MAX_BYTES", 100)
    result = update_service.download_latest_release(str(tmp_path))
    assert result.ok is False
    assert "上限" in result.message
    assert list(tmp_path.iterdir()) == []


def test_download_empty_payload_fails(monkeypatch, tmp_path):
    """下载内容为空：不落盘，报可读失败"""
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service, "_fetch_releases", fake_fetch(RELEASE_WITH_FPK())
    )
    monkeypatch.setattr(update_service, "_open_download", fake_download(b""))
    result = update_service.download_latest_release(str(tmp_path))
    assert result.ok is False
    assert "内容为空" in result.message


def test_download_no_fpk_asset_fails(monkeypatch, tmp_path):
    """Release 只有校验文件没有 fpk：不产出空文件，给可读失败"""
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch([make_release("v0.8.0", assets=("MD5SUMS.txt",))]),
    )
    result = update_service.download_latest_release(str(tmp_path))
    assert result.ok is False
    assert "没有可下载的 fpk" in result.message


def test_download_dest_must_exist(monkeypatch, tmp_path):
    """目标目录不存在：直接失败，不越权创建目录"""
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service, "_fetch_releases", fake_fetch(RELEASE_WITH_FPK())
    )
    result = update_service.download_latest_release(str(tmp_path / "nope"))
    assert result.ok is False
    assert "目标目录" in result.message
    assert not (tmp_path / "nope").exists()


def test_download_follows_selected_channel(monkeypatch, tmp_path):
    """渠道选择同样作用于下载：dev 线取测试包，release 线不碰预发布"""
    payload = b"pkg"
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch(
            [
                make_release("v0.8.0", assets=("fn-finstat-v0.8.0.fpk",)),
                make_release(
                    "v0.9.0-dev.1.g0",
                    prerelease=True,
                    assets=("fn-finstat-v0.9.0-dev.1.g0.fpk",),
                ),
            ]
        ),
    )
    monkeypatch.setattr(update_service, "_open_download", fake_download(payload))
    dev = update_service.download_latest_release(str(tmp_path), channel="dev")
    assert dev.version == "0.9.0-dev.1.g0"
    rel = update_service.download_latest_release(str(tmp_path), channel="release")
    assert rel.version == "0.8.0"


def test_api_download_to_nas_contract(client, monkeypatch, tmp_path):
    """接口把配置的下载目录交给下载服务：fpk 落进目录，响应携带落盘路径"""
    payload = b"pkg"
    md5 = hashlib.md5(payload).hexdigest()
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service, "_fetch_releases", fake_fetch(RELEASE_WITH_FPK())
    )
    monkeypatch.setattr(update_service, "_open_download", fake_download(payload))
    monkeypatch.setattr(
        update_service, "_fetch_bytes", fake_checksum(md5, "fn-finstat-v0.8.0.fpk")
    )
    monkeypatch.setattr(
        update_service,
        "load_update_settings",
        lambda: UpdateSettings(download_dir=str(tmp_path)),
    )
    resp = client.post(
        "/api/update/download",
        json={"source": "github", "channel": "release"},
        headers=B_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["ok"] is True
    assert data["md5_verified"] is True
    assert data["saved_path"] == str(tmp_path / "fn-finstat-v0.8.0.fpk")
    assert (tmp_path / "fn-finstat-v0.8.0.fpk").read_bytes() == payload


def test_api_download_without_configured_dir_refuses(client, monkeypatch):
    """未配置下载目录：200 + ok=False + 指引配置的可读原因，不抛 5xx"""
    monkeypatch.setattr(
        update_service, "load_update_settings", lambda: UpdateSettings(download_dir="")
    )
    resp = client.post("/api/update/download", json={}, headers=B_HEADERS)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is False
    assert "尚未配置" in data["message"]


def test_api_download_with_missing_dir_refuses(client, monkeypatch, tmp_path):
    """已配置但目录不可访问：拒绝下载并提示检查配置，不悄悄换地方落盘"""
    missing = tmp_path / "nope"
    monkeypatch.setattr(
        update_service,
        "load_update_settings",
        lambda: UpdateSettings(download_dir=str(missing)),
    )
    resp = client.post("/api/update/download", json={}, headers=B_HEADERS)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is False
    assert "不存在或不可访问" in data["message"]


def test_api_download_dir_config_roundtrip(client, monkeypatch, tmp_path):
    """配置读写：管理员 PUT 保存（相对路径 400），GET 按身份回显"""
    saved = {}

    def _fake_save(settings):
        saved["dir"] = settings.download_dir

    monkeypatch.setattr(update_service, "save_update_settings", _fake_save)
    # 读取侧同样走内存桩：返回「已保存」的配置，模拟保存后立即生效
    monkeypatch.setattr(
        update_service,
        "load_update_settings",
        lambda: UpdateSettings(download_dir=saved.get("dir", "")),
    )

    # 相对路径被拒绝（400 + 统一错误码）
    res = client.put(
        "/api/update/download-dir",
        json={"download_dir": "relative/path"},
        headers=A_HEADERS,
    )
    assert res.status_code == 400
    assert "绝对路径" in res.json()["msg"]
    assert saved == {}

    # 管理员保存成功：拿完整路径回显
    res = client.put(
        "/api/update/download-dir",
        json={"download_dir": str(tmp_path)},
        headers=A_HEADERS,
    )
    assert res.status_code == 200
    assert res.json()["data"]["configured"] is True
    assert res.json()["data"]["download_dir"] == str(tmp_path)
    assert saved["dir"] == str(tmp_path)

    # 普通账号 GET：只回目录名，不暴露服务器绝对路径（与账单目录同策略）
    res = client.get("/api/update/download-dir", headers=B_HEADERS)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["configured"] is True
    assert data["download_dir"] == tmp_path.name
    assert str(tmp_path) not in str(data)

    # 管理员 GET：完整路径
    res = client.get("/api/update/download-dir", headers=A_HEADERS)
    assert res.json()["data"]["download_dir"] == str(tmp_path)


def test_api_download_dir_config_admin_only(client):
    """保存目录是应用级配置：普通账号 PUT 被拒（403），与账单目录同策略"""
    res = client.put(
        "/api/update/download-dir",
        json={"download_dir": "/vol1/1000/fpk"},
        headers=B_HEADERS,
    )
    assert res.status_code == 403


# ---- 下载通道的受限重定向（P1 回归：GitHub 附件 302 → 附件存储域）----
#
# 此前 _open_download/_fetch_bytes 复用 Release 接口的 _NoRedirect，GitHub 附件
# 直链 302 到 release-assets.githubusercontent.com 被直接判失败，「下载到 NAS」
# 永远不可用。修复后走 _TrustedRedirect：https + 白名单域 + 次数上限。
# 测试用假 HTTPS handler 替代真实网络：302 与文件本体都由内存响应给出，
# 但 302 → 跟随判定 → 二次请求的完整链路走真实 opener 与重定向处理器。


def _fake_https_response(url: str, code: int, body: bytes = b"", location: str = ""):
    import email.message
    import urllib.response

    headers = email.message.Message()
    if location:
        headers["Location"] = location
    resp = urllib.response.addinfourl(io.BytesIO(body), headers, url, code)
    resp.msg = "Found" if code == 302 else "OK"
    return resp


def _stub_https_302(location_by_url: dict[str, str], bodies: dict[str, bytes]):
    """假 HTTPS 层：命中 location_by_url 的请求回 302，其余回 200 + 字节流"""

    class _StubHTTPS(urllib.request.HTTPSHandler):
        def https_open(self, req):
            location = location_by_url.get(req.full_url)
            if location is not None:
                return _fake_https_response(req.full_url, 302, location=location)
            return _fake_https_response(
                req.full_url, 200, body=bodies.get(req.full_url, b"")
            )

    return _StubHTTPS()


GITHUB_ASSET_URL = "https://github.com/zhangyilin_233/fn-finstat/releases/download/v0.8.0/fn-finstat-v0.8.0.fpk"
GITHUB_CDN_URL = (
    "https://release-assets.githubusercontent.com/secret/fn-finstat-v0.8.0.fpk"
)


def test_download_follows_github_asset_redirect(monkeypatch, tmp_path):
    """GitHub 附件直链 302 → 附件存储域：受限重定向跟随，下载完成（P1 回归）"""
    payload = b"fpk-behind-redirect"
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    # make_release 固定拼 gitee 直链，这里直接给 GitHub 形态的 Release 记录
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch(
            [
                {
                    "tag_name": "v0.8.0",
                    "name": "fn-finstat v0.8.0",
                    "prerelease": False,
                    "created_at": "2026-09-17T14:54:26+08:00",
                    "body": "",
                    "assets": [
                        {
                            "name": "fn-finstat-v0.8.0.fpk",
                            "browser_download_url": GITHUB_ASSET_URL,
                        }
                    ],
                }
            ]
        ),
    )
    stub = _stub_https_302(
        {GITHUB_ASSET_URL: GITHUB_CDN_URL},
        {GITHUB_CDN_URL: payload},
    )
    opener = urllib.request.build_opener(update_service._TrustedRedirect(), stub)
    monkeypatch.setattr(update_service, "_DOWNLOAD_OPENER", opener)

    result = update_service.download_latest_release(str(tmp_path))
    assert result.ok is True, result.message
    assert result.file_name == "fn-finstat-v0.8.0.fpk"
    assert Path(result.saved_path).read_bytes() == payload
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example.com/pk",  # 白名单外主机
        "http://release-assets.githubusercontent.com/pk",  # 白名单域但明文 http
        "https://github.com.evil.com/pk",  # 仿冒后缀的同形域名
    ],
)
def test_download_refuses_redirect_outside_allowlist(monkeypatch, tmp_path, location):
    """302 指向白名单外 / 明文 http / 仿冒域：不跟随，按既有失败路径暴露"""
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service, "_fetch_releases", fake_fetch(RELEASE_WITH_FPK())
    )
    stub = _stub_https_302(
        {f"{DOWNLOAD_PREFIX}/v0.8.0/fn-finstat-v0.8.0.fpk": location}, {}
    )
    opener = urllib.request.build_opener(update_service._TrustedRedirect(), stub)
    monkeypatch.setattr(update_service, "_DOWNLOAD_OPENER", opener)

    result = update_service.download_latest_release(str(tmp_path))
    assert result.ok is False
    assert "接口返回 302" in result.message
    assert list(tmp_path.iterdir()) == []


def test_trusted_redirect_policy_unit_boundaries():
    """redirect_request 判定单元边界：白名单 https 跟随，其余一律返回 None"""
    handler = update_service._TrustedRedirect()
    req = urllib.request.Request(GITHUB_ASSET_URL)
    followed = handler.redirect_request(req, None, 302, "Found", None, GITHUB_CDN_URL)
    assert isinstance(followed, urllib.request.Request)
    assert followed.full_url == GITHUB_CDN_URL
    for bad in [
        "https://objects.githubusercontent.com/ok",  # 白名单内对象存储域同样放行
        "https://gitee.com/o/r/releases/download/v1/f.fpk",
    ]:
        assert isinstance(
            handler.redirect_request(req, None, 302, "Found", None, bad),
            urllib.request.Request,
        )


# ---- 并发下载与交付（P2 回归：唯一临时文件 + 交付锁）----


def test_download_concurrent_same_target_is_safe(monkeypatch, tmp_path):
    """两个请求并发下载同一安装包：各写各的临时文件，交付互斥，双方成功且文件完整

    修复前所有请求共享固定名 .part：先完成的一方清理/改名会踩进另一方的
    写入流（复现为 FileNotFoundError / 交付残件）。
    """
    import threading

    payload_a, payload_b = b"a" * 65536, b"b" * 65536
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service,
        "_fetch_releases",
        fake_fetch([make_release("v0.8.0", assets=("fn-finstat-v0.8.0.fpk",))]),
    )

    gate = threading.Barrier(2, timeout=15)  # 两个请求都开流后才开始写，制造重叠
    payloads = [payload_a, payload_b]
    started: list[int] = []
    lock = threading.Lock()

    def _open(url: str, timeout: float = 0):
        with lock:
            index = len(started)
            started.append(index)
        gate.wait()
        return io.BytesIO(payloads[index])

    monkeypatch.setattr(update_service, "_open_download", _open)

    results: list = []

    def _run():
        results.append(update_service.download_latest_release(str(tmp_path)))

    threads = [threading.Thread(target=_run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 2
    assert all(r.ok for r in results), [r.message for r in results]
    target = tmp_path / "fn-finstat-v0.8.0.fpk"
    assert target.exists()
    # 交付互斥 + 各写各的临时文件：终态必须是某一方 payload 的完整内容，
    # 绝不能是 a/b 交错的半截文件
    assert target.read_bytes() in (payload_a, payload_b)
    assert not list(tmp_path.glob("*.part"))


def test_download_deliver_failure_reports_readable(monkeypatch, tmp_path):
    """交付（改名）失败：给可读失败并清理残件，不抛 5xx 不留半截包"""
    monkeypatch.setattr(update_service, "APP_VERSION", "0.7.1")
    monkeypatch.setattr(
        update_service, "_fetch_releases", fake_fetch(RELEASE_WITH_FPK())
    )
    monkeypatch.setattr(update_service, "_open_download", fake_download(b"pkg"))

    def _boom(src, dst):
        raise PermissionError(13, "目录不可写")

    monkeypatch.setattr(update_service.os, "replace", _boom)
    result = update_service.download_latest_release(str(tmp_path))
    assert result.ok is False
    assert "保存失败" in result.message
    assert list(tmp_path.iterdir()) == []
