#!/usr/bin/env python3
"""从 Git 提交历史自动生成 Release 说明（releaseNode.txt）、CHANGELOG.md 与 RELEASE_NOTES.md。

三份产物分工：
  - releaseNode.txt   本次构建的分组日志 + SHA-256，作 Release **附件**（CI 生成）
  - CHANGELOG.md      累积历史，仓库里的变更日志正文（`--update-changelog`）
  - RELEASE_NOTES.md  只含本次版本，作 Release **描述**的数据源（`--update-changelog`）
    （描述必须取自入库文件，而累积的 CHANGELOG 贴到发布页会让读者看到历史版本日志，
      故单出一份。两者同批生成，不会各自漂移。）

设计约束（与项目既有脚本保持一致）：
  - 仅依赖 Python 标准库，CI 环境无需额外装包（同 scripts/sync_version.py）
  - 遵循约定式提交（Conventional Commits），无法识别的提交归入"其他变更"，绝不丢提交
  - 破坏性变更只认标题上的 ! 标记（正文的 BREAKING CHANGE 读不到，也不做子串匹配）
  - 兼容浅克隆：无法定位基线时自动退化为最近 N 个提交
  - 幂等：同一版本重复运行会覆盖 CHANGELOG 中已有的同名段落
  - 统一 LF 换行（见 write_text_lf）

基线（本次日志的起点）推断优先级：
  1. 命令行 --from <ref>
  2. CHANGELOG.md 顶部段落里的 <!-- release-baseline: <sha> --> 标记
  3. 最近的语义化版本 tag（形如 v1.2.3；构建号 tag v39 与测试版 tag
     v1.2.3-dev.4 都会被跳过）
  4. 最近 --limit 个提交

用法：
  python3 scripts/gen_release_notes.py                      # 打印到标准输出
  python3 scripts/gen_release_notes.py -o releaseNode.txt   # 写入文件（CI 用）
  python3 scripts/gen_release_notes.py --update-changelog   # 更新 CHANGELOG.md 与 RELEASE_NOTES.md
  python3 scripts/gen_release_notes.py --from v0.6.0 --to HEAD
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 分组定义

# 顺序即输出顺序：重要的放前面
GROUP_ORDER: list[tuple[str, str]] = [
    ("security", "🔒 安全修复"),
    ("feat", "✨ 新功能"),
    ("fix", "🐛 问题修复"),
    ("perf", "⚡ 性能优化"),
    ("refactor", "♻️ 代码重构"),
    ("test", "🧪 测试"),
    ("ci", "👷 构建与流水线"),
    ("build", "📦 打包构建"),
    ("docs", "📝 文档"),
    ("style", "🎨 样式调整"),
    ("chore", "🔧 杂项维护"),
    ("revert", "⏪ 版本回退"),
]
BREAKING_TITLE = "💥 破坏性变更"
OTHER_TITLE = "📌 其他变更"

# 常见 type 别名 → 标准分组
TYPE_ALIASES = {
    "feature": "feat",
    "features": "feat",
    "feat": "feat",
    "bugfix": "fix",
    "hotfix": "fix",
    "fixes": "fix",
    "fix": "fix",
    "sec": "security",
    "security": "security",
    "perf": "perf",
    "performance": "perf",
    "refactor": "refactor",
    "refactoring": "refactor",
    "test": "test",
    "tests": "test",
    "ci": "ci",
    "build": "build",
    "doc": "docs",
    "docs": "docs",
    "style": "style",
    "chore": "chore",
    "chores": "chore",
    "deps": "chore",
    "revert": "revert",
}

# 约定式提交：type(scope)!: subject   —— 同时兼容中文冒号
COMMIT_RE = re.compile(
    r"^(?P<type>[A-Za-z]+)"
    r"(?:\((?P<scope>[^)]*)\))?"
    r"(?P<breaking>!)?"
    r"\s*[:：]\s*"
    r"(?P<subject>.+)$"
)

BASELINE_RE = re.compile(r"<!--\s*release-baseline:\s*(?P<sha>[0-9a-fA-F]{6,40})\s*-->")
# 段落**自身**的起点。与 release-baseline（供"下次从哪开始"推断）是两件事：
# 后者随每次生成前移，若拿它当起点重生成，同一版本的段落会被"截断"成只剩
# 自上次生成以来的提交（实测：0.7.3 段落从 13 个提交缩成 2 个）。
RELEASE_START_RE = re.compile(
    r"<!--\s*release-start:\s*(?P<sha>[0-9a-fA-F]{6,40})\s*-->"
)
# 正式发版 tag：形如 v1.2.3。刻意用 $ 锚定、不接受预发布后缀——
# dev 流水线每次推送都会打一个 v0.7.1-dev.N.ghash 的 tag，若把它们也算成
# 发版基线，正式版日志的起点会被"最后一次测试构建"截断，中间合入 main 的
# 变更会凭空消失。构建号 tag（v39）同样被排除在外。
SEMVER_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")

SEP = "\x1f"  # git log 字段分隔符，避免与提交信息冲突


# ---------------------------------------------------------------- Git 封装


def run_git(*args: str) -> str | None:
    """执行 git 命令，失败时返回 None（脚本要能容忍 CI 环境的各种怪状态）。"""
    try:
        out = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def repo_root() -> Path:
    top = run_git("rev-parse", "--show-toplevel")
    return Path(top) if top else Path(__file__).resolve().parent.parent


def is_shallow() -> bool:
    return run_git("rev-parse", "--is-shallow-repository") == "true"


def ref_exists(ref: str) -> bool:
    return run_git("rev-parse", "--verify", "--quiet", ref) is not None


def current_sha() -> str:
    return run_git("rev-parse", "HEAD") or ""


def write_text_lf(path: Path, text: str) -> None:
    """显式写 LF 换行

    `Path.write_text` 在 Windows 会按 `os.linesep` 写成 CRLF，而仓库里所有文本都是
    LF（提交时 git 也会规范化）。工作区留下一份 CRLF 只会让后续 diff、校验与
    「同一文件两份换行」的困惑反复出现。
    """
    with open(path, "w", encoding="utf-8", newline="\n") as fp:
        fp.write(text)


def resolve_baseline(changelog: Path, limit: int) -> tuple[str, str]:
    """返回 (起点 ref, 推断方式说明)。"""
    # 2. CHANGELOG 基线标记
    if changelog.exists():
        match = BASELINE_RE.search(changelog.read_text(encoding="utf-8"))
        if match and ref_exists(match.group("sha")):
            return match.group("sha"), "CHANGELOG 基线标记"

    # 3. 最近的语义化版本 tag（跳过 v39 这类构建号 tag）
    tags = run_git("tag", "--list", "--sort=-creatordate")
    if tags:
        for tag in tags.splitlines():
            tag = tag.strip()
            if tag and SEMVER_TAG_RE.match(tag) and ref_exists(tag):
                return tag, f"语义化版本 tag {tag}"

    # 4. 兜底：最近 limit 个提交
    return f"HEAD~{limit}", f"最近 {limit} 个提交"


def collect_commits(start: str, end: str, limit: int) -> tuple[list[dict], str]:
    """采集提交。范围为空时自动放宽为最近 limit 个提交。"""
    fmt = f"--pretty=%H{SEP}%s{SEP}%an{SEP}%h"

    def parse(raw: str) -> list[dict]:
        items = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            parts = line.split(SEP)
            if len(parts) < 4:
                continue
            items.append(
                {
                    "sha": parts[0],
                    "subject": parts[1],
                    "author": parts[2],
                    "short": parts[3],
                }
            )
        return items

    raw = run_git("log", "--no-merges", "--reverse", fmt, f"{start}..{end}")
    commits = parse(raw or "")
    if commits:
        return commits, f"{start}..{end}"

    # 范围为空（例如基线就是 HEAD，或浅克隆拿不到历史）→ 退化为最近 N 个提交
    fallback = run_git("log", "--no-merges", "--reverse", "-n", str(limit), fmt, end)
    commits = parse(fallback or "")
    return commits, f"最近 {limit} 个提交"


# ---------------------------------------------------------------- 解析与渲染


def group_commits(commits: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {key: [] for key, _ in GROUP_ORDER}
    buckets[BREAKING_TITLE] = []
    buckets[OTHER_TITLE] = []

    for commit in commits:
        match = COMMIT_RE.match(commit["subject"])
        if not match:
            buckets[OTHER_TITLE].append(commit)
            continue

        ctype = match.group("type").lower()
        scope = (match.group("scope") or "").strip()
        breaking = bool(match.group("breaking"))

        commit["scope"] = scope
        commit["clean_subject"] = match.group("subject").strip()
        # 破坏性变更单独成组，同时保留在原始分组中。
        # 只认标题上显式的 ! 标记：不能用 "BREAKING CHANGE" in subject 这类子串判断，
        # 否则 "docs: 补充 BREAKING CHANGE 章节说明" 会被误判为破坏性变更。
        if breaking:
            buckets[BREAKING_TITLE].append(commit)

        key = TYPE_ALIASES.get(ctype)
        (buckets[key] if key else buckets[OTHER_TITLE]).append(commit)

    return buckets


def render_entry(commit: dict) -> str:
    scope = commit.get("scope")
    subject = commit.get("clean_subject") or commit["subject"]
    prefix = f"**{scope}**: " if scope else ""
    return f"- {prefix}{subject} ({commit['short']})"


def render(
    version: str,
    commits: list[dict],
    range_desc: str,
    sha256: str = "",
    build_number: str = "",
    date_str: str = "",
    channel: str = "release",
    alias: str = "",
) -> str:
    date = date_str or datetime.now().strftime("%Y-%m-%d")
    buckets = group_commits(commits)

    authors = sorted({c["author"] for c in commits if c.get("author")})
    head = f"## fn-finstat v{version}"
    if build_number:
        head += f"（构建 #{build_number}）"

    lines: list[str] = [head, ""]
    # 测试版（dev 渠道）在描述最前面给出醒目声明：Release 附件是可直接下载安装的，
    # 用户往往不看 tag 名只看正文，这里必须让他知道拿到的是测试包。
    if channel == "dev":
        lines.append(
            "> 🧪 **测试版本**：由 dev 分支自动构建，仅供验证使用，"
            "请勿作为正式版本分发。"
        )
        lines.append("")
    meta = [f"📅 发布日期：{date}", f"🔢 提交数量：{len(commits)}"]
    if authors:
        meta.append(f"👥 贡献者：{'、'.join(authors)}")
    lines.append("> " + " · ".join(meta))
    lines.append("")

    if not commits:
        lines.append("本次发布没有检测到新的提交记录。")
        lines.append("")
    else:
        for key, title in GROUP_ORDER:
            items = buckets.get(key) or []
            if not items:
                continue
            lines.append(f"### {title}")
            lines.append("")
            lines.extend(render_entry(c) for c in items)
            lines.append("")

        for title in (BREAKING_TITLE, OTHER_TITLE):
            items = buckets.get(title) or []
            if not items:
                continue
            lines.append(f"### {title}")
            lines.append("")
            lines.extend(render_entry(c) for c in items)
            lines.append("")

    lines.append("---")
    lines.append("")
    asset = f"fn-finstat-v{version}.fpk"
    if alias:
        lines.append(
            f"**安装**：下载附件 `{alias}`（该渠道的固定入口）"
            f"或 `{asset}`（本次构建），在飞牛 OS 应用中心手动安装。"
        )
    else:
        # 没给别名说明这份正文要**同时**服务于两个渠道（发布说明文件是入库的，
        # dev 与 release 的 Release 都会把它当描述）。此时按渠道写死其中一个别名，
        # 必然在另一个渠道的页面上指向不存在的附件 —— 改为把两个稳定入口都讲清。
        lines.append(
            "**安装**：在飞牛 OS 应用中心手动安装本 Release 的 fpk 附件 —— "
            "正式版为 `fn-finstat-latest.fpk`、测试版为 `fn-finstat-dev.fpk`，"
            f"`{asset}` 为本次构建的带版本号副本。"
        )
    # 校验指引必须写在描述正文里：附件列表里只有孤零零的 MD5SUMS.txt，
    # 用户不一定知道它是干什么用的，更不会知道要用 md5sum -c 去跑
    lines.append(
        "**校验（MD5）**：下载附件 `MD5SUMS.txt`，与 fpk 放在同一目录后执行 "
        "`md5sum -c MD5SUMS.txt`（macOS 用 `md5 -c MD5SUMS.txt`）。"
    )
    if sha256:
        lines.append(f"**校验（SHA-256）**：`{sha256}`")
    lines.append(f"**变更范围**：{range_desc}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- CHANGELOG


def section_pattern(version: str) -> re.Pattern[str]:
    """匹配某版本的段落（从 `## ` 标题到下一个 `## ` 之前）

    两处必须收紧，否则会波及相邻版本的段落：

    1. 版本号后必须跟非数字、非点号的字符（或行尾）—— 否则 `v0.7.1` 会命中
       `v0.7.10`，生成 0.7.1 的日志时把 0.7.10 的段落整段替换掉。
       测试版标题形如 `v0.7.1-dev.42.g1a2b3c4`，其后的 `-` 不触发该断言。
    2. 标题部分用 `[^\\n]` 而不是 `.`：整个正则带 DOTALL，用 `.` 会让标题段
       跨行去后面找版本号，匹配起点被提前到**上一个版本**的标题上
       （实测：取 0.7.1 的段落却把 0.7.10 的标题与内容一起圈进来）。
    """
    return re.compile(
        rf"^## [^\n]*?v{re.escape(version)}(?![0-9.])[^\n]*$.*?(?=^## |\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )


def version_section(text: str, version: str) -> str:
    """取出 text 中该版本对应的段落（含标题）；不存在时返回空串"""
    match = section_pattern(version).search(text)
    return match.group(0) if match else ""


def pinned_start(changelog: Path, version: str) -> str:
    """该版本段落已记录的起点 sha（没有则空串）

    同一版本被反复生成时（dev 渠道每次 push 前都要重生成，好让发布页与
    应用内「更新说明」跟上最新提交）必须沿用它 —— 否则段落会越跑越短。
    """
    if not changelog.exists():
        return ""
    section = version_section(changelog.read_text(encoding="utf-8"), version)
    match = RELEASE_START_RE.search(section)
    if match and ref_exists(match.group("sha")):
        return match.group("sha")
    return ""


def update_changelog(
    changelog: Path,
    version: str,
    body: str,
    baseline: str,
    start: str = "",
) -> None:
    header = (
        "# 更新日志\n\n"
        "本文件由 `scripts/gen_release_notes.py` 自动生成，请勿手工编辑已发布版本的内容。\n"
        "提交信息请遵循[约定式提交](https://www.conventionalcommits.org/zh-hans/)。\n"
    )
    section = body + f"\n<!-- release-baseline: {baseline} -->\n"
    if start:
        # 记下本段落的起点，供同版本重生成时沿用（见 pinned_start）
        section += f"<!-- release-start: {start} -->\n"

    if not changelog.exists():
        write_text_lf(changelog, f"{header}\n{section}")
        return

    text = changelog.read_text(encoding="utf-8")

    # 已存在同名版本段 → 替换（保证重复运行幂等）
    pattern = section_pattern(version)
    if pattern.search(text):
        text = pattern.sub(lambda _: section, text, count=1)
    else:
        # 否则插到第一个版本段之前
        first = re.search(r"^## ", text, flags=re.MULTILINE)
        if first:
            text = text[: first.start()] + section + "\n" + text[first.start() :]
        else:
            text = text.rstrip() + "\n\n" + section

    write_text_lf(changelog, text)


def update_release_notes(path: Path, body: str) -> None:
    """写「发布说明」文件：**只含本次版本**，作为 Release 描述的数据源

    为什么需要单独一个文件（而不是继续把整份 CHANGELOG 当描述）：

    Release 描述取自仓库里的文件，而 CHANGELOG 按定义是**累积**的 —— 整份贴到
    Release 页面上，读者第一眼看到的可能是几个版本之前的日志（dev 渠道每次 push
    都发 Release，最先撞上的就是这个）。把描述换成只含本次版本的文件，页面才等于
    「本次改了什么」。

    不带表头（CHANGELOG 的「本文件由…生成」在发布页面上只是噪音）；正文本身以
    `## fn-finstat v{版本}` 开头，正好是发布页的一级小节。
    """
    write_text_lf(path, body if body.endswith("\n") else body + "\n")


# ---------------------------------------------------------------- 入口


def read_version(root: Path) -> str:
    version_file = root / "VERSION"
    if version_file.exists():
        version = version_file.read_text(encoding="utf-8").strip()
        if version:
            return version
    return "0.0.0"


def main() -> int:
    parser = argparse.ArgumentParser(description="自动生成 Release 说明与 CHANGELOG")
    parser.add_argument("-o", "--output", help="输出文件路径（默认打印到标准输出）")
    parser.add_argument("--from", dest="from_ref", help="日志起点 ref（默认自动推断）")
    parser.add_argument(
        "--to", dest="to_ref", default="HEAD", help="日志终点 ref，默认 HEAD"
    )
    parser.add_argument("--tag", help="版本号（默认读取 VERSION 文件）")
    parser.add_argument(
        "--channel",
        default="release",
        choices=("release", "dev"),
        help="构建渠道；dev 会在说明顶部标注「测试版本」（默认 release）",
    )
    parser.add_argument("--date", help="发布日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--sha256", default="", help="产物 SHA-256，写入安装校验说明")
    parser.add_argument(
        "--alias",
        default="",
        help="渠道别名产物文件名（如 fn-finstat-latest.fpk），写入安装说明；缺省时只提带版本号副本",
    )
    parser.add_argument(
        "--limit", type=int, default=30, help="无法推断基线时的兜底提交数，默认 30"
    )
    parser.add_argument(
        "--update-changelog",
        action="store_true",
        help="把本次说明写入 CHANGELOG.md（同版本会覆盖）",
    )
    parser.add_argument("--changelog", default="CHANGELOG.md", help="CHANGELOG 路径")
    parser.add_argument(
        "--release-notes",
        default="RELEASE_NOTES.md",
        help="发布说明路径（只含本次版本，作 Release 描述的数据源）；空串则不写",
    )
    args = parser.parse_args()

    root = repo_root()
    os.chdir(root)

    if is_shallow():
        print("⚠️  检测到浅克隆，提交历史可能不完整", file=sys.stderr)

    version = args.tag or read_version(root)
    # 构建号只影响日志标题：CI_* 为通用变量（GitHub Actions 等），GITEE_* 为 Gitee Go
    build_number = os.environ.get("CI_BUILD_NUMBER") or os.environ.get(
        "GITEE_PIPELINE_BUILD_NUMBER", ""
    )

    changelog = root / args.changelog
    # 同版本重复生成时沿用该段落已记录的起点（--from 显式指定时以它为准）
    pinned = "" if args.from_ref else pinned_start(changelog, version)
    if args.from_ref:
        start, source = args.from_ref, "--from 指定"
        if not ref_exists(start):
            print(f"⚠️  起点 {start} 不存在，改用自动推断", file=sys.stderr)
            start, source = resolve_baseline(changelog, args.limit)
    elif pinned:
        start, source = pinned, "该版本已记录的起点"
    else:
        start, source = resolve_baseline(changelog, args.limit)

    commits, range_desc = collect_commits(start, args.to_ref, args.limit)
    if not commits:
        print("⚠️  未采集到任何提交，Release 说明将只包含概要", file=sys.stderr)

    print(
        f"==> 版本 {version} · 基线 {start}（{source}）· {len(commits)} 个提交",
        file=sys.stderr,
    )

    body = render(
        version=version,
        commits=commits,
        range_desc=range_desc,
        sha256=args.sha256,
        build_number=build_number,
        date_str=args.date,
        channel=args.channel,
        alias=args.alias,
    )

    if args.output:
        out = Path(args.output)
        write_text_lf(out, body)
        print(f"==> 已写入 {out}", file=sys.stderr)
    else:
        print(body)

    if args.update_changelog:
        update_changelog(
            changelog, version, body, current_sha() or args.to_ref, start=start
        )
        print(f"==> 已更新 {changelog}", file=sys.stderr)
        # 发布说明与 CHANGELOG 同源同批写出：一个是累积历史，一个只含本次版本。
        # Release 描述取后者，页面才等于「本次改了什么」。
        if args.release_notes:
            notes = root / args.release_notes
            update_release_notes(notes, body)
            print(f"==> 已更新 {notes}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
