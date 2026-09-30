"""应用更新检查：查询 Release、与本机版本比对、给出下载入口；并支持把最新
安装包直接下载到 NAS 目录

为什么默认是「检查 + 引导下载」而不是自动升级：
    飞牛第三方应用的安装与升级由系统应用中心完成（fpk 由平台解压到
    ${TRIM_APPDEST}），应用进程既无权也不应去替换自身的安装目录。因此这里只
    回答三个问题：有没有新版本、新版改了什么、去哪里下载，实际安装仍由用户
    在应用中心手动完成。

    「下载到 NAS」是引导下载的增强而非自动升级：后端把最新 fpk（优先带版本
    号副本——带唯一标识的交付物）拉到用户的授权目录，用户在文件管理器就能
    拿到本地包，再去应用中心手动安装，安装环节仍不越权。

版本比较自行实现（semver 2.0 语义），不新增运行时依赖：
    dev 渠道的版本号形如 `0.7.1-dev.2.g8c2979a`，必须按预发布规则排序 ——
    预发布版本小于同号正式版（`0.7.1-dev.x` < `0.7.1`），否则装了测试包的用户
    会被永远提示「有新版本」；而预发布段内的数字段又要按数值比较
    （`dev.10` > `dev.2`，按字符串比会得出相反结论）。两者任一算错都表现为
    「更新提示乱跳」，所以这里不省事。

渠道（版本线）由本机版本号推导（含预发布段即测试渠道），不引入配置项：
    正式渠道只看正式 Release，绝不把测试包推给正式用户；测试渠道与全部
    Release 一起比较，因此正式版发布后测试用户也能收到升级提示。
    接口允许显式指定（channel=release|dev），供用户主动切换版本线查看。

发布站点（source）可显式选择 github / gitee，默认 github：
    两条流水线对同一 tag 在两个站点各发一份 Release，附件与更新说明同源，
    任一站点检查到的结论一致。默认 github 只是首选入口；Gitee 在国内网络
    更稳，作为可切换的备用源。站点间结果缓存互相独立（按 source+channel 键）。
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import APP_REPO_URL, APP_REPO_URL_GITHUB, APP_VERSION
from app.core.errors import ErrorCode, ValidationError
from app.file_settings import UpdateSettings, load_update_settings, save_update_settings
from app.schemas.update import (
    UpdateCheckResult,
    UpdateDownloadDirOut,
    UpdateDownloadResult,
)

logger = logging.getLogger(__name__)

# ---- 版本渠道（与 scripts/sync_version.py 的渠道词一致）----
CHANNEL_RELEASE = "release"
CHANNEL_DEV = "dev"

# ---- 发布站点 ----
SOURCE_GITHUB = "github"
SOURCE_GITEE = "gitee"
DEFAULT_SOURCE = SOURCE_GITHUB


def _repo_path(url: str, site: str) -> str:
    """从仓库地址截取 owner/repo（站点域名之后的部分）"""
    return url.split(f"{site}.com/", 1)[-1].strip("/")


# 每个发布站点一段配置：Release 接口、Release 页面地址均由仓库地址推导。
# 两站点的 Release API 同构（Gitee v5 即 GitHub API 的镜像），解析逻辑共用。
_RELEASE_SOURCES = {
    SOURCE_GITEE: {
        "repo_url": APP_REPO_URL,
        "api_url": f"https://gitee.com/api/v5/repos/{_repo_path(APP_REPO_URL, 'gitee')}/releases",
        "page_url": f"{APP_REPO_URL}/releases",
    },
    SOURCE_GITHUB: {
        "repo_url": APP_REPO_URL_GITHUB,
        "api_url": f"https://api.github.com/repos/{_repo_path(APP_REPO_URL_GITHUB, 'github')}/releases",
        "page_url": f"{APP_REPO_URL_GITHUB}/releases",
    },
}

# 单页条数：Gitee 匿名接口有限流，取最近 20 条足够覆盖两个渠道
# （dev 渠道每次 push 产出一个 Release，正式版发布频率低得多）
RELEASES_PAGE_SIZE = 20
# 外部请求超时（秒）：更新检查是「顺手一查」，不能拖住设置页
REQUEST_TIMEOUT = 10
# 结果缓存时长（秒）：设置页每次打开都会自动检查，不缓存会把接口额度耗在反复刷新上
CACHE_TTL = 300
# 手动「重新检查」的服务端节流（秒）：refresh 会绕过结果缓存直连更新源，
# 接口对所有登录账号开放，不设下限时高频点击会把 Gitee 匿名接口的额度耗光
REFRESH_MIN_INTERVAL = 60
# Release 正文摘录上限（字符）
NOTES_MAX_LENGTH = 600
# MD5 校验文件名（构建产物固定名，见 scripts/build_fpk.sh）
CHECKSUM_FILE = "MD5SUMS.txt"

USER_AGENT = f"fn-finstat/{APP_VERSION} ({APP_REPO_URL})"

# 三段式 semver（含可选预发布段与构建段）。刻意要求 major.minor.patch 齐全：
# 仓库里存在 v / v23 / v33 这类历史构建号 tag，只写 major 的话 `v23` 会被解析成
# 23.0.0 并永远被判为「最新」，把更新提示彻底带偏。
_VERSION_RE = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.\-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.\-]+))?$"
)
# Changelog 一级小节（`## `）：Release 描述是整篇 CHANGELOG.md，需截出本次版本
_SECTION_RE = re.compile(r"^##\s", re.MULTILINE)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止跟随重定向（与 ai_service / notify_service 同策略）

    更新源是固定地址，正常响应不需要跳转；禁跟随可避免被 30x 引到其他主机，
    也让「接口被改址」这类问题直接暴露为可读的失败提示而不是静默取到别处内容。

    必须显式重写 redirect_request 返回 None：只继承不重写时 urllib 仍按默认
    策略跟随 3xx（安全审计实测），返回 None 使 3xx 以 HTTPError 抛出、由
    check_for_update 的既有降级分支转成可读结果。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)

# 附件下载域白名单：Release 接口返回的附件直链在 GitHub 上并不直接给出文件
# 本体，而是 302 到对象存储域（release-assets / objects.githubusercontent.com）；
# Gitee 的附件由 gitee.com 自身直接提供。只对这些 https 域允许跟随跳转。
_DOWNLOAD_REDIRECT_HOSTS = frozenset(
    {
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "gitee.com",
    }
)


class _TrustedRedirect(urllib.request.HTTPRedirectHandler):
    """下载通道的受限重定向：只跟随白名单域名的 https 跳转

    安装包/校验文件的下载不能沿用 _NoRedirect：GitHub 附件直链会 302 到
    release-assets.githubusercontent.com 才给出文件本体，一刀切禁跳转让
    「下载到 NAS」永远失败（302 被直接判为失败）。但完全放开跳转又会重蹈
    安全审计 M5-5 的覆辙——30x 把出站请求引向任意主机。折中即受限策略：
    https + 白名单域 + 跳转次数上限，目标不满足时返回 None，urllib 按
    「未处理的 3xx」走默认错误分支抛 HTTPError，与禁跳失败路径完全一致。
    跳转链自身的环路防护仍由基类（max_repeats / max_redirections）兜底。
    """

    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urllib.parse.urlsplit(newurl)
        if parts.scheme != "https":
            return None
        if (parts.hostname or "") not in _DOWNLOAD_REDIRECT_HOSTS:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_DOWNLOAD_OPENER = urllib.request.build_opener(_TrustedRedirect())


# ---- 版本号解析与比较（semver 2.0，零依赖）----


def normalize_version(text: str) -> str:
    """去掉 tag 的 v 前缀，返回规范版本号；不是三段式版本号时返回空串"""
    raw = (text or "").strip()
    if raw[:1] in ("v", "V"):
        raw = raw[1:]
    return raw if _VERSION_RE.match(raw) else ""


def _pre_parts(pre: str) -> tuple[tuple[int, object], ...]:
    """预发布段 → 可比较的标识符序列；无预发布段返回空元组

    每个标识符编码为 (类型, 值)：0=纯数字（按数值比较），1=含字母（按字典序）。
    类型位保证 semver §11.4.3「数字标识符永远低于非数字标识符」自动成立。

    空串必须返回 ()：`"".split(".")` 会得到 `[""]`，把「没有预发布段」误当成
    「预发布段含一个空标识符」，于是 `0.7.1` 与 `0.7.1-dev.2` 的大小关系整体反转
    —— 装了正式版反而被提示升级、装了测试版却提示已是最新。
    """
    if not pre:
        return ()
    parts: list[tuple[int, object]] = []
    for piece in pre.split("."):
        parts.append((0, int(piece)) if piece.isdigit() else (1, piece))
    return tuple(parts)


def _parse(text: str) -> Optional[tuple]:
    """版本号 → (major, minor, patch, 预发布标识符元组)；非法时返回 None"""
    raw = normalize_version(text)
    if not raw:
        return None
    match = _VERSION_RE.match(raw)
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        _pre_parts(match.group("pre") or ""),
    )


def compare_versions(left: str, right: str) -> Optional[int]:
    """按 semver 2.0 比较：left 更新返回 1，相同返回 0，更旧返回 -1

    任一侧不是合法三段式版本号时返回 None —— 调用方据此跳过该条目，
    绝不能把「比不了」当作「相同」，否则历史 tag 会被当成最新版本或被静默忽略。
    """
    a, b = _parse(left), _parse(right)
    if a is None or b is None:
        return None
    for x, y in zip(a[:3], b[:3]):
        if x != y:
            return 1 if x > y else -1
    pre_a, pre_b = a[3], b[3]
    if not pre_a or not pre_b:
        # 无预发布段 > 有预发布段（0.7.1 > 0.7.1-dev.2）
        if pre_a == pre_b:
            return 0
        return 1 if not pre_a else -1
    for x, y in zip(pre_a, pre_b):
        if x != y:
            return 1 if x > y else -1
    # 前缀全同：标识符多者更大（1.0.0-a.1 > 1.0.0-a）
    if len(pre_a) == len(pre_b):
        return 0
    return 1 if len(pre_a) > len(pre_b) else -1


def channel_of(version: str) -> str:
    """由版本号推导发布渠道：带预发布段（-dev）的是测试渠道

    运行中的版本号本身就是渠道标识（sync_version.py 给 dev 渠道产物追加
    `-dev.N.gSHA` 段），因此不引入配置项 —— 也就不会被配错。
    """
    parsed = _parse(version)
    if parsed and parsed[3]:
        return CHANNEL_DEV
    return CHANNEL_RELEASE


# ---- Release 解析 ----


@dataclass(frozen=True)
class RemoteRelease:
    """一条 Release 中更新检查关心的字段"""

    version: str
    tag: str
    name: str
    published_at: str
    prerelease: bool
    notes: str
    download_url: str
    checksum_url: str
    page_url: str


def _asset_url(assets: list, name: str) -> str:
    for asset in assets:
        if isinstance(asset, dict) and str(asset.get("name") or "") == name:
            return str(asset.get("browser_download_url") or "").strip()
    return ""


def _pick_download_url(assets: list, version: str) -> str:
    """挑 fpk 下载直链

    顺序即优先级：先取「与所展示版本号严格对应的带版本号副本」—— 两个渠道的
    流水线都硬断言它存在，也最能回答「下到的到底是哪一次构建」；其次裸名
    fn-finstat.fpk（早期正式 Release 的挂法）；最后退到任意 fpk。
    刻意不去识别渠道别名（fn-finstat-latest.fpk / fn-finstat-dev.fpk）：别名与
    同 Release 内的其它 fpk 内容完全一致，为它在本模块再维护一份渠道词表，
    只会多一处需要与 scripts/sync_version.py 对齐的地方。
    """
    url = _asset_url(assets, f"fn-finstat-v{version}.fpk")
    if url:
        return url
    url = _asset_url(assets, "fn-finstat.fpk")
    if url:
        return url
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        if str(asset.get("name") or "").endswith(".fpk"):
            return str(asset.get("browser_download_url") or "").strip()
    return ""


def _excerpt(text: str, limit: int = NOTES_MAX_LENGTH) -> str:
    body = (text or "").strip()
    if len(body) <= limit:
        return body
    return body[:limit].rstrip() + "…"


def _sections(text: str) -> list[str]:
    """按 `## ` 一级小节切分正文（小节标题保留在结果里）"""
    starts = [match.start() for match in _SECTION_RE.finditer(text)]
    result = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        result.append(text[start:end].strip())
    return result


def _release_notes(body: str, version: str) -> str:
    """从 Release 正文里截出「本次版本」那一段；认不出本次版本时返回空串

    Release 描述直接取的仓库 CHANGELOG.md（见 .workflow/build-fpk.yml），整篇含
    全部历史版本，所以要截出其中一段。**关键是不能无脑取第一段**：CHANGELOG 是
    发版时才更新的产物，Release 却可能来自更靠后的构建（dev 渠道每次 push 都发），
    此时第一段是**上一个已发布版本**的日志 —— 原样展示等于把旧版日志冒充本次说明，
    比「没有说明」更糟（用户会以为那些变更就是本次的）。

    因此按**基版本**（去掉预发布段，如 `0.7.3-dev.5.g4cc0a6f` → `0.7.3`）去匹配小节
    标题：测试包与同基线正式版共用同一份 CHANGELOG 小节，这是设计如此。匹配不上
    就返回空串，界面据此隐藏说明区块 —— 宁可少显示，不可错标。
    """
    base = normalize_version(version).split("-", 1)[0]
    if not base:
        # 版本号不可解析（理论到不了这里）：退回第一段，好过什么都不给
        sections = _sections((body or "").strip())
        return _excerpt(sections[0]) if sections else ""
    # 边界断言防「0.7.1 命中 0.7.10」：前后都不能紧邻数字或点
    pattern = re.compile(rf"(?<![\d.]){re.escape(base)}(?![\d.])")
    for section in _sections((body or "").strip()):
        heading = section.splitlines()[0] if section else ""
        if pattern.search(heading):
            return _excerpt(section)
    logger.info(
        "更新说明未匹配到本次版本 %s 的小节，已隐藏（CHANGELOG 可能落后于构建）", base
    )
    return ""


def parse_release(raw: dict, source: str = DEFAULT_SOURCE) -> Optional[RemoteRelease]:
    """把一条 Release 记录转成 RemoteRelease；tag 无法解析为三段式版本号时返回 None

    两个站点的 Release 记录同构（Gitee v5 API 即 GitHub API 的镜像），字段
    含义一致，解析逻辑共用；只有详情页 URL 前缀按站点分派。
    """
    if not isinstance(raw, dict):
        return None
    tag = str(raw.get("tag_name") or "").strip()
    version = normalize_version(tag)
    if not version:
        # v / v23 这类历史构建号 tag 直接剔除（见 _VERSION_RE 注释）
        return None
    assets = raw.get("assets")
    assets = assets if isinstance(assets, list) else []
    return RemoteRelease(
        version=version,
        tag=tag,
        name=str(raw.get("name") or "").strip() or tag,
        published_at=str(raw.get("created_at") or "").strip(),
        prerelease=bool(raw.get("prerelease")),
        notes=_release_notes(str(raw.get("body") or ""), version),
        download_url=_pick_download_url(assets, version),
        checksum_url=_asset_url(assets, CHECKSUM_FILE),
        page_url=f"{_RELEASE_SOURCES[source]['page_url']}/tag/{tag}",
    )


def _fetch_releases(source: str, timeout: float = REQUEST_TIMEOUT) -> list:
    """请求指定站点的 Release 列表，返回原始数组

    单独成函数便于测试替换（单测一律 monkeypatch 本函数，不发起真实网络请求）。
    """
    url = f"{_RELEASE_SOURCES[source]['api_url']}?page=1&per_page={RELEASES_PAGE_SIZE}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET",
    )
    with _OPENER.open(request, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("接口未返回 Release 列表")
    return data


def _newest(releases: list[RemoteRelease]) -> Optional[RemoteRelease]:
    """取版本号最大的一条；版本号不可比较的条目自动跳过"""
    best: Optional[RemoteRelease] = None
    for item in releases:
        if best is None or compare_versions(item.version, best.version) == 1:
            best = item
    return best


# ---- 结果缓存（设置页每次打开都会自动检查一次）----
# 键为 (source, channel)：站点与版本线的组合各自缓存，切换选择互不挤掉
_cache_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[float, UpdateCheckResult]] = {}
_last_refresh_at: float = 0.0  # 上次强制刷新的单调时钟时刻（节流用，全站共用）


def clear_cache() -> None:
    """清空结果缓存与刷新节流（测试用；用户侧「重新检查」走 refresh 参数）"""
    global _cache, _last_refresh_at
    with _cache_lock:
        _cache = {}
        _last_refresh_at = 0.0


def _result(
    current: str,
    channel: str,
    source: str,
    latest: Optional[RemoteRelease],
) -> UpdateCheckResult:
    """按远端版本与本机版本的关系组装结果；远端缺记录时降级为 ok=False"""
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    if latest is None:
        return UpdateCheckResult(
            ok=False,
            message="更新服务器上没有可用的版本记录",
            current_version=current,
            channel=channel,
            source=source,
            checked_at=checked_at,
        )
    diff = compare_versions(latest.version, current)
    if diff is None:
        relation = "unknown"
        message = f"无法比较版本号（本机 {current}，远端 {latest.version}）"
    elif diff > 0:
        relation = "newer"
        message = f"发现新版本 v{latest.version}"
    elif diff == 0:
        relation = "same"
        message = "已是最新版本"
    else:
        relation = "older"
        message = "当前版本比已发布版本更新（本机为未发布构建）"
    return UpdateCheckResult(
        ok=True,
        message=message,
        current_version=current,
        channel=channel,
        source=source,
        latest_version=latest.version,
        relation=relation,
        release_name=latest.name,
        published_at=latest.published_at,
        notes=latest.notes,
        download_url=latest.download_url,
        checksum_url=latest.checksum_url,
        page_url=latest.page_url,
        checked_at=checked_at,
    )


def _failure(
    current: str, channel: str, source: str, message: str
) -> UpdateCheckResult:
    """检查失败的结果（不抛错，理由见 schemas/update.py 的 docstring）"""
    return UpdateCheckResult(
        ok=False,
        message=message,
        current_version=current,
        channel=channel,
        source=source,
        checked_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


def _latest_release(
    source: str, channel: str, timeout: float
) -> Optional[RemoteRelease]:
    """拉取指定站点与版本线下的最新 Release（检查与下载共用）

    正式渠道只看正式 Release：测试包不能推给正式用户；测试渠道与全部 Release
    一起比较，正式版发布后测试用户也能收到提示。
    网络异常原样上抛，由调用方按各自场景降级（检查回 ok=False，下载回 ok=False）。
    """
    releases = [
        item
        for item in (
            parse_release(raw, source) for raw in _fetch_releases(source, timeout)
        )
        if item is not None
    ]
    pool = (
        releases
        if channel == CHANNEL_DEV
        else [r for r in releases if not r.prerelease]
    )
    return _newest(pool)


def check_for_update(
    refresh: bool = False,
    timeout: float = REQUEST_TIMEOUT,
    channel: Optional[str] = None,
    source: str = DEFAULT_SOURCE,
) -> UpdateCheckResult:
    """检查是否有新版本；网络不可用时返回 ok=False 的结果而非抛错

    refresh=True 绕过缓存（用户手动点「重新检查」时用）；默认复用 CACHE_TTL 秒内的
    上次结果，避免反复进设置页把发布站点的匿名接口额度耗光。
    强制刷新自身按 REFRESH_MIN_INTERVAL 节流：间隔内的再次刷新直接回缓存结果
    （检查接口对所有登录账号开放，不能让任何账号无限直连远端）。

    channel：release=只看正式版 / dev=与全部 Release 比较；缺省由本机版本号推导
    （默认「当前版本」语义）。source：发布站点，默认 github。

    失败结果同样进缓存：离线环境下每次进设置页都重试一遍会白等一个超时。
    """
    global _cache, _last_refresh_at
    current = APP_VERSION
    if source not in _RELEASE_SOURCES:
        # 失败结果也要过 schema 的 Literal 校验，source 只能用合法值占位；
        # 用户传的非法值保留在 message 里（接口层已用 Literal 拦绝大多数）
        return _failure(
            current,
            channel if channel in (CHANNEL_RELEASE, CHANNEL_DEV) else CHANNEL_RELEASE,
            DEFAULT_SOURCE,
            f"未知的发布站点：{source}",
        )
    # 显式渠道只认合法值，其余（含 None）一律回退「当前版本」语义；
    # 接口层已用 Literal 校验，这里兜底服务层的直接调用方
    channel = (
        channel if channel in (CHANNEL_RELEASE, CHANNEL_DEV) else channel_of(current)
    )
    key = (source, channel)
    with _cache_lock:
        cached = _cache.get(key)
        cache_fresh = cached is not None and time.monotonic() - cached[0] < CACHE_TTL
        refresh_throttled = (
            refresh
            and cached is not None
            and time.monotonic() - _last_refresh_at < REFRESH_MIN_INTERVAL
        )
    if refresh and refresh_throttled:
        return cached[1].model_copy(update={"cached": True})
    if not refresh and cache_fresh:
        return cached[1].model_copy(update={"cached": True})
    if refresh:
        with _cache_lock:
            _last_refresh_at = time.monotonic()

    try:
        latest = _latest_release(source, channel, timeout)
    except urllib.error.HTTPError as exc:
        logger.warning("更新检查失败：%s 接口返回 %s", source, exc.code)
        # GitHub 匿名额度按 IP 每小时 60 次，Gitee 匿名接口同样有限流：
        # 403/429 大概率是额度耗尽，提示里点明，免得用户当成本地故障反复重试
        hint = "（接口限流，请稍后再试）" if exc.code in (403, 429) else ""
        result = _failure(
            current, channel, source, f"更新接口返回 {exc.code}{hint}，请稍后重试"
        )
    except TimeoutError:
        logger.warning("更新检查失败：连接更新服务器超时")
        result = _failure(
            current, channel, source, f"连接更新服务器超时（>{timeout:g}s）"
        )
    except urllib.error.URLError as exc:
        logger.warning("更新检查失败：%s", exc.reason)
        result = _failure(current, channel, source, f"无法连接更新服务器：{exc.reason}")
    except OSError as exc:
        # SSL 握手失败、连接被重置等非 URLError 的底层错误
        logger.warning("更新检查失败：%s", exc)
        result = _failure(
            current, channel, source, "无法连接更新服务器，请检查设备网络"
        )
    except (ValueError, UnicodeDecodeError) as exc:
        logger.warning("更新检查失败：更新接口响应异常（%s）", exc)
        result = _failure(current, channel, source, "更新接口响应异常，请稍后重试")
    else:
        result = _result(current, channel, source, latest)

    with _cache_lock:
        _cache[key] = (time.monotonic(), result)
    return result


# ---- 安装包下载目录（用户显式配置，配置前下载功能不可用）----


def _dir_display_name(path: str) -> str:
    """对外展示的目录名：只取最后一级，不暴露服务器绝对路径（与 NAS 账单目录同策略）"""
    return Path(path).name or path


def get_download_dir_config(reveal_full_path: bool = True) -> UpdateDownloadDirOut:
    """当前下载目录配置；未配置时 configured=False（下载接口据此拒绝）"""
    download_dir = load_update_settings().download_dir
    shown = download_dir if reveal_full_path else _dir_display_name(download_dir)
    return UpdateDownloadDirOut(
        download_dir=shown,
        configured=bool(download_dir),
        exists=bool(download_dir) and Path(download_dir).is_dir(),
    )


def set_download_dir(
    download_dir: str, owner_user_id: str = ""
) -> UpdateDownloadDirOut:
    """保存下载目录：仅接受绝对路径（资源管理器复制的地址常带引号，顺手剥掉）；空串=清除配置

    应用级共享配置，与账单目录同策略由管理员维护；普通账号的下载只消费该配置。
    """
    cleaned = (download_dir or "").strip().strip('"').strip()
    if cleaned:
        path = Path(cleaned).expanduser()
        if not path.is_absolute():
            raise ValidationError("下载目录必须是绝对路径", code=ErrorCode.BAD_REQUEST)
        cleaned = str(path)
    save_update_settings(UpdateSettings(download_dir=cleaned))
    logger.info(
        "账号%s更新安装包下载目录：%s",
        f" {owner_user_id} " if owner_user_id else " ",
        cleaned or "（清除配置）",
    )
    return UpdateDownloadDirOut(
        download_dir=cleaned,
        configured=bool(cleaned),
        exists=bool(cleaned) and Path(cleaned).is_dir(),
    )


def resolve_download_dir() -> tuple[str, str]:
    """解析可用的下载目录；返回 (目录, 问题提示)，目录就绪时提示为空串

    未配置 → 提示先配置（本功能只在用户显式配置过的目录里落盘，
    不回退到飞牛授权目录或应用私有目录冒充交付位置）；
    已配置但不可访问 → 提示联系管理员（完整路径只进服务端日志）。
    """
    download_dir = load_update_settings().download_dir
    if not download_dir:
        return (
            "",
            "尚未配置安装包保存目录：请管理员在「设置 → 关于 → 版本更新」中配置后再试",
        )
    if not Path(download_dir).is_dir():
        logger.warning("下载目录不可访问：%s", download_dir)
        return "", "安装包保存目录当前不存在或不可访问，请联系管理员检查配置"
    return download_dir, ""


# ---- 下载安装包到 NAS ----

# 下载读超时（秒）：安装包几十 MB 量级，按「单次 read 无数据」计——卡死 30 秒
# 即认为连接中断，总时长由文件大小上限天然约束
DOWNLOAD_READ_TIMEOUT = 30
# 单文件大小上限：远超正常 fpk 体积（几十 MB）即视为异常响应，及时止损
DOWNLOAD_MAX_BYTES = 512 * 1024 * 1024
# 落盘文件名的白名单字符：附件名来自远端 Release，落 NAS 前收敛到安全集合
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _failure_download(channel: str, source: str, message: str) -> UpdateDownloadResult:
    """下载失败的结果（不抛错，与检查失败同策略）"""
    return UpdateDownloadResult(
        ok=False,
        message=message,
        source=source,
        channel=channel,
    )


def _fetch_bytes(url: str, timeout: float) -> bytes:
    """拉取小文件（MD5 校验文件）的完整字节；重定向按下载通道的受限策略处理"""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT}, method="GET"
    )
    with _DOWNLOAD_OPENER.open(request, timeout=timeout) as resp:
        return resp.read()


def _open_download(url: str, timeout: float):
    """打开安装包下载流（返回 file-like，调用方负责 close）；单独成函数便于测试替换

    使用 _DOWNLOAD_OPENER：附件直链需要 302 到对象存储域，受限重定向策略见
    _TrustedRedirect（Release 接口本身仍走 _NoRedirect，两者不可混用）。
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT}, method="GET"
    )
    return _DOWNLOAD_OPENER.open(request, timeout=timeout)


def _verify_md5(
    checksum_url: str, file_name: str, data_md5: str, timeout: float
) -> tuple[bool, bool]:
    """用 Release 附带的 MD5SUMS.txt 校验落盘文件

    返回 (verified, checked)：checked=False 表示没有校验文件或拉取失败——
    此时**不能**当作校验失败处理（安装包本身可能完好，交由用户知情使用）；
    checked=True 且 verified=False 才是真正的校验不匹配，调用方应丢弃文件。
    """
    if not checksum_url:
        return False, False
    try:
        text = _fetch_bytes(checksum_url, timeout).decode("utf-8", errors="replace")
    except OSError as exc:
        logger.warning("拉取 MD5 校验文件失败（不阻断交付）：%s", exc)
        return False, False
    for line in text.splitlines():
        # gen_checksums.py 的输出格式：<32位小写md5><两个空格><文件名>
        parts = line.split("  ", 1)
        if len(parts) == 2 and parts[1].strip() == file_name:
            return parts[0].strip().lower() == data_md5.lower(), True
    return False, False


# 交付锁：同一目标文件的「临时 → 正式名」改名互斥。并发下载同一安装包时
# 各请求写各自的临时文件（见下），但最终都改名到同一个正式名，必须串行化，
# 避免交错出半截文件冒充完整包。锁按目标路径分配、常驻不回收——键集合以
# 「下载过的文件」为上界（个位数量级），不构成泄漏。
_delivery_locks: dict[str, threading.Lock] = {}
_delivery_locks_guard = threading.Lock()


def _delivery_lock(target: Path) -> threading.Lock:
    key = str(target)
    with _delivery_locks_guard:
        return _delivery_locks.setdefault(key, threading.Lock())


def download_latest_release(
    dest_dir: str,
    source: str = DEFAULT_SOURCE,
    channel: Optional[str] = None,
    timeout: float = REQUEST_TIMEOUT,
) -> UpdateDownloadResult:
    """把指定站点/版本线的最新安装包下载到 NAS 目录（fpk 落盘 + MD5 校验）

    与 check_for_update 的分工：检查只给直链引导浏览器下载；本函数由**后端**
    拉流落盘，供用户在飞牛文件管理器里拿本地 fpk 走应用中心安装。
    下载地址始终由本模块从 Release 附件里解析（优先带版本号副本——「带唯一
    标识」的交付物），绝不接受调用方传 URL，避免把后端变成任意地址代理。

    落盘安全：先写同目录临时文件再原子改名，进程中断不会留下半截 fpk 冒充
    完整包；MD5 与 Release 校验文件不匹配时删除落盘文件并报失败——残缺包
    宁可不交付。dest_dir 来自用户配置的下载目录（resolve_download_dir 解析并
    确认可访问），本函数只信任传入值，不自行挑选落盘位置。
    """
    channel = (
        channel
        if channel in (CHANNEL_RELEASE, CHANNEL_DEV)
        else channel_of(APP_VERSION)
    )
    if source not in _RELEASE_SOURCES:
        return _failure_download(channel, DEFAULT_SOURCE, f"未知的发布站点：{source}")
    dest = Path(dest_dir)
    if not dest.is_dir():
        return _failure_download(
            channel, source, f"目标目录不存在或不可访问：{dest_dir}"
        )

    try:
        release = _latest_release(source, channel, timeout)
    except urllib.error.HTTPError as exc:
        hint = "（接口限流，请稍后再试）" if exc.code in (403, 429) else ""
        return _failure_download(
            channel, source, f"更新接口返回 {exc.code}{hint}，请稍后重试"
        )
    except (TimeoutError, urllib.error.URLError, OSError):
        logger.warning("下载安装包失败：无法连接 %s", source)
        return _failure_download(channel, source, "无法连接更新服务器，请检查设备网络")
    except (ValueError, UnicodeDecodeError) as exc:
        logger.warning("下载安装包失败：更新接口响应异常（%s）", exc)
        return _failure_download(channel, source, "更新接口响应异常，请稍后重试")

    if release is None:
        return _failure_download(channel, source, "更新服务器上没有可用的版本记录")
    if not release.download_url:
        return _failure_download(
            channel, source, f"v{release.version} 的 Release 没有可下载的 fpk 附件"
        )

    # 文件名取附件名并做白名单收敛：正常情形即 fn-finstat-v{版本}.fpk
    raw_name = release.download_url.rstrip("/").rsplit("/", 1)[-1]
    file_name = _SAFE_NAME_RE.sub("_", raw_name) or f"fn-finstat-v{release.version}.fpk"
    if not file_name.endswith(".fpk"):
        file_name += ".fpk"
    target = dest / file_name

    md5_hasher = hashlib.md5()
    size = 0
    # 先写请求内唯一的临时文件再原子改名：并发下载同一包时各写各的 .part
    # （共享固定名会让一个请求的清理/改名踩进另一个请求的写入流），进程
    # 中断只会留下 .part 残件，不会冒充完整包
    part_path = target.with_name(f"{target.name}.{uuid.uuid4().hex}.part")
    try:
        with (
            _open_download(release.download_url, DOWNLOAD_READ_TIMEOUT) as resp,
            part_path.open("wb") as out,
        ):
            while chunk := resp.read(256 * 1024):
                size += len(chunk)
                if size > DOWNLOAD_MAX_BYTES:
                    raise ValueError(
                        f"安装包超过 {DOWNLOAD_MAX_BYTES // (1024 * 1024)}MB 上限，已中止"
                    )
                md5_hasher.update(chunk)
                out.write(chunk)
    except urllib.error.HTTPError as exc:
        part_path.unlink(missing_ok=True)
        hint = "（接口限流，请稍后再试）" if exc.code in (403, 429) else ""
        return _failure_download(
            channel, source, f"下载失败：接口返回 {exc.code}{hint}"
        )
    except (TimeoutError, urllib.error.URLError, OSError) as exc:
        part_path.unlink(missing_ok=True)
        logger.warning("下载安装包失败：%s", exc)
        return _failure_download(
            channel, source, "下载失败：连接中断，请检查设备网络后重试"
        )
    except ValueError as exc:
        part_path.unlink(missing_ok=True)
        return _failure_download(channel, source, str(exc))

    data_md5 = md5_hasher.hexdigest()
    if size == 0:
        part_path.unlink(missing_ok=True)
        return _failure_download(channel, source, "下载内容为空，请稍后重试")

    md5_verified, md5_checked = _verify_md5(
        release.checksum_url, file_name, data_md5, timeout
    )
    if md5_checked and not md5_verified:
        # 有校验文件但对不上 = 传输残缺/文件被换：删除残件，宁可不交付
        part_path.unlink(missing_ok=True)
        return _failure_download(
            channel, source, f"MD5 校验不匹配（{file_name}），已丢弃本次下载，请重试"
        )

    # 交付加锁：同一目标文件的改名互斥（并发下载同一包时不串台）；改名本身
    # 也可能因目录权限等失败，纳入失败分支并清理残件
    with _delivery_lock(target):
        try:
            os.replace(part_path, target)
        except OSError as exc:
            part_path.unlink(missing_ok=True)
            logger.warning("安装包交付失败：%s", exc)
            return _failure_download(
                channel, source, "安装包保存失败，请检查目录权限后重试"
            )
    logger.info("安装包已下载到 NAS：%s（%s 字节，md5 %s）", target, size, data_md5)
    if md5_verified:
        message = f"已下载 v{release.version} 并通过 MD5 校验"
    elif md5_checked:
        message = (
            f"已下载 v{release.version}（MD5 校验文件里没有该附件的条目，未校验完整性）"
        )
    else:
        message = f"已下载 v{release.version}（该 Release 未附可用的 MD5 校验文件，未校验完整性）"
    return UpdateDownloadResult(
        ok=True,
        message=message,
        version=release.version,
        file_name=file_name,
        dest_dir=str(dest),
        saved_path=str(target),
        size=size,
        md5_verified=md5_verified,
        source=source,
        channel=channel,
    )
