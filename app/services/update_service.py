"""应用更新检查：查询 Gitee Release、与本机版本比对、给出下载入口

为什么是「检查 + 引导下载」而不是自动升级：
    飞牛第三方应用的安装与升级由系统应用中心完成（fpk 由平台解压到
    ${TRIM_APPDEST}），应用进程既无权也不应去替换自身的安装目录。因此这里只
    回答三个问题：有没有新版本、新版改了什么、去哪里下载，实际安装仍由用户
    在应用中心手动完成。

版本比较自行实现（semver 2.0 语义），不新增运行时依赖：
    dev 渠道的版本号形如 `0.7.1-dev.2.g8c2979a`，必须按预发布规则排序 ——
    预发布版本小于同号正式版（`0.7.1-dev.x` < `0.7.1`），否则装了测试包的用户
    会被永远提示「有新版本」；而预发布段内的数字段又要按数值比较
    （`dev.10` > `dev.2`，按字符串比会得出相反结论）。两者任一算错都表现为
    「更新提示乱跳」，所以这里不省事。

渠道由本机版本号推导（含预发布段即测试渠道），不引入配置项：
    正式渠道只看正式 Release，绝不把测试包推给正式用户；测试渠道与全部
    Release 一起比较，因此正式版发布后测试用户也能收到升级提示。
"""

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.config import APP_REPO_URL, APP_VERSION
from app.schemas.update import UpdateCheckResult

logger = logging.getLogger(__name__)

# ---- 发布渠道（与 scripts/sync_version.py 的渠道词一致）----
CHANNEL_RELEASE = "release"
CHANNEL_DEV = "dev"

# 仓库归属只由 config.APP_REPO_URL 一处声明，接口与页面地址均由它推导
_REPO_PATH = APP_REPO_URL.split("gitee.com/", 1)[-1].strip("/")
RELEASES_API_URL = f"https://gitee.com/api/v5/repos/{_REPO_PATH}/releases"
RELEASES_PAGE_URL = f"{APP_REPO_URL}/releases"

# 单页条数：Gitee 匿名接口有限流，取最近 20 条足够覆盖两个渠道
# （dev 渠道每次 push 产出一个 Release，正式版发布频率低得多）
RELEASES_PAGE_SIZE = 20
# 外部请求超时（秒）：更新检查是「顺手一查」，不能拖住设置页
REQUEST_TIMEOUT = 10
# 结果缓存时长（秒）：设置页每次打开都会自动检查，不缓存会把接口额度耗在反复刷新上
CACHE_TTL = 300
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
    """


_OPENER = urllib.request.build_opener(_NoRedirect)


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


def _release_notes(body: str) -> str:
    """从 Release 正文里截出「本次版本」那一段

    Release 描述直接取的仓库 CHANGELOG.md（见 .workflow/build-fpk.yml），整篇含
    全部历史版本；原样展示会把几十个旧版本一起端给用户，因此只取第一个 `## `
    小节（CHANGELOG 的最新版本在最前，由 gen_release_notes.py --update-changelog
    保持）。正文格式非预期时不截，退回整体摘录。
    """
    text = (body or "").strip()
    starts = [match.start() for match in _SECTION_RE.finditer(text)]
    if starts:
        end = starts[1] if len(starts) > 1 else len(text)
        text = text[starts[0] : end].strip()
    return _excerpt(text)


def parse_release(raw: dict) -> Optional[RemoteRelease]:
    """把一条 Release 记录转成 RemoteRelease；tag 无法解析为三段式版本号时返回 None"""
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
        notes=_release_notes(str(raw.get("body") or "")),
        download_url=_pick_download_url(assets, version),
        checksum_url=_asset_url(assets, CHECKSUM_FILE),
        page_url=f"{RELEASES_PAGE_URL}/tag/{tag}",
    )


def _fetch_releases(timeout: float = REQUEST_TIMEOUT) -> list:
    """请求 Gitee Release 列表，返回原始数组

    单独成函数便于测试替换（单测一律 monkeypatch 本函数，不发起真实网络请求）。
    """
    url = f"{RELEASES_API_URL}?page=1&per_page={RELEASES_PAGE_SIZE}"
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

_cache_lock = threading.Lock()
_cache: Optional[tuple[float, UpdateCheckResult]] = None


def clear_cache() -> None:
    """清空结果缓存（测试用；用户侧「重新检查」走 refresh 参数）"""
    global _cache
    with _cache_lock:
        _cache = None


def _result(
    current: str, channel: str, latest: Optional[RemoteRelease]
) -> UpdateCheckResult:
    """按远端版本与本机版本的关系组装结果；远端缺记录时降级为 ok=False"""
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    if latest is None:
        return UpdateCheckResult(
            ok=False,
            message="更新服务器上没有可用的版本记录",
            current_version=current,
            channel=channel,
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


def _failure(current: str, channel: str, message: str) -> UpdateCheckResult:
    """检查失败的结果（不抛错，理由见 schemas/update.py 的 docstring）"""
    return UpdateCheckResult(
        ok=False,
        message=message,
        current_version=current,
        channel=channel,
        checked_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


def check_for_update(
    refresh: bool = False,
    timeout: float = REQUEST_TIMEOUT,
) -> UpdateCheckResult:
    """检查是否有新版本；网络不可用时返回 ok=False 的结果而非抛错

    refresh=True 绕过缓存（用户手动点「重新检查」时用）；默认复用 CACHE_TTL 秒内的
    上次结果，避免反复进设置页把 Gitee 匿名接口的额度耗光。

    失败结果同样进缓存：离线环境下每次进设置页都重试一遍会白等一个超时。
    """
    global _cache
    current = APP_VERSION
    channel = channel_of(current)
    if not refresh:
        with _cache_lock:
            cached = _cache
        if cached and time.monotonic() - cached[0] < CACHE_TTL:
            return cached[1].model_copy(update={"cached": True})

    try:
        releases = [
            item
            for item in (parse_release(raw) for raw in _fetch_releases(timeout))
            if item is not None
        ]
    except urllib.error.HTTPError as exc:
        logger.warning("更新检查失败：更新接口返回 %s", exc.code)
        result = _failure(current, channel, f"更新接口返回 {exc.code}，请稍后重试")
    except TimeoutError:
        logger.warning("更新检查失败：连接更新服务器超时")
        result = _failure(current, channel, f"连接更新服务器超时（>{timeout:g}s）")
    except urllib.error.URLError as exc:
        logger.warning("更新检查失败：%s", exc.reason)
        result = _failure(current, channel, f"无法连接更新服务器：{exc.reason}")
    except OSError as exc:
        # SSL 握手失败、连接被重置等非 URLError 的底层错误
        logger.warning("更新检查失败：%s", exc)
        result = _failure(current, channel, "无法连接更新服务器，请检查设备网络")
    except (ValueError, UnicodeDecodeError) as exc:
        logger.warning("更新检查失败：更新接口响应异常（%s）", exc)
        result = _failure(current, channel, "更新接口响应异常，请稍后重试")
    else:
        # 正式渠道只看正式 Release：测试包不能推给正式用户；
        # 测试渠道与全部 Release 一起比较，正式版发布后测试用户也能收到提示。
        pool = (
            releases
            if channel == CHANNEL_DEV
            else [r for r in releases if not r.prerelease]
        )
        result = _result(current, channel, _newest(pool))

    with _cache_lock:
        _cache = (time.monotonic(), result)
    return result
