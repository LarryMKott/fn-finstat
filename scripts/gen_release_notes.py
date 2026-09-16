#!/usr/bin/env python3
"""从 Git 提交历史自动生成 Release 说明（releaseNode.txt）与 CHANGELOG.md。

设计约束（与项目既有脚本保持一致）：
  - 仅依赖 Python 标准库，CI 环境无需额外装包（同 scripts/sync_version.py）
  - 遵循约定式提交（Conventional Commits），无法识别的提交归入"其他变更"，绝不丢提交
  - 兼容浅克隆：无法定位基线时自动退化为最近 N 个提交
  - 幂等：同一版本重复运行会覆盖 CHANGELOG 中已有的同名段落

基线（本次日志的起点）推断优先级：
  1. 命令行 --from <ref>
  2. CHANGELOG.md 顶部段落里的 <!-- release-baseline: <sha> --> 标记
  3. 最近的语义化版本 tag（形如 v1.2.3；构建号 tag v39 会被跳过）
  4. 最近 --limit 个提交

用法：
  python3 scripts/gen_release_notes.py                      # 打印到标准输出
  python3 scripts/gen_release_notes.py -o releaseNode.txt   # 写入文件（CI 用）
  python3 scripts/gen_release_notes.py --update-changelog   # 同时更新 CHANGELOG.md
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
SEMVER_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+")

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
                {"sha": parts[0], "subject": parts[1], "author": parts[2], "short": parts[3]}
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
        # 破坏性变更单独成组，同时保留在原始分组中
        if breaking or "BREAKING CHANGE" in commit["subject"].upper():
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
) -> str:
    date = date_str or datetime.now().strftime("%Y-%m-%d")
    buckets = group_commits(commits)

    authors = sorted({c["author"] for c in commits if c.get("author")})
    head = f"## fn-finstat v{version}"
    if build_number:
        head += f"（构建 #{build_number}）"

    lines: list[str] = [head, ""]
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
    lines.append(f"**安装**：下载附件 `{asset}`，在飞牛 OS 应用中心手动安装。")
    if sha256:
        lines.append(f"**校验（SHA-256）**：`{sha256}`")
    lines.append(f"**变更范围**：{range_desc}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- CHANGELOG


def update_changelog(changelog: Path, version: str, body: str, baseline: str) -> None:
    header = (
        "# 更新日志\n\n"
        "本文件由 `scripts/gen_release_notes.py` 自动生成，请勿手工编辑已发布版本的内容。\n"
        "提交信息请遵循[约定式提交](https://www.conventionalcommits.org/zh-hans/)。\n"
    )
    section = body + f"\n<!-- release-baseline: {baseline} -->\n"

    if not changelog.exists():
        changelog.write_text(f"{header}\n{section}", encoding="utf-8")
        return

    text = changelog.read_text(encoding="utf-8")

    # 已存在同名版本段 → 替换（保证重复运行幂等）
    pattern = re.compile(
        rf"^## .*?v{re.escape(version)}.*?$.*?(?=^## |\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    if pattern.search(text):
        text = pattern.sub(lambda _: section, text, count=1)
    else:
        # 否则插到第一个版本段之前
        first = re.search(r"^## ", text, flags=re.MULTILINE)
        if first:
            text = text[: first.start()] + section + "\n" + text[first.start() :]
        else:
            text = text.rstrip() + "\n\n" + section

    changelog.write_text(text, encoding="utf-8")


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
    parser.add_argument("--to", dest="to_ref", default="HEAD", help="日志终点 ref，默认 HEAD")
    parser.add_argument("--tag", help="版本号（默认读取 VERSION 文件）")
    parser.add_argument("--date", help="发布日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--sha256", default="", help="产物 SHA-256，写入安装校验说明")
    parser.add_argument(
        "--limit", type=int, default=30, help="无法推断基线时的兜底提交数，默认 30"
    )
    parser.add_argument(
        "--update-changelog",
        action="store_true",
        help="把本次说明写入 CHANGELOG.md（同版本会覆盖）",
    )
    parser.add_argument("--changelog", default="CHANGELOG.md", help="CHANGELOG 路径")
    args = parser.parse_args()

    root = repo_root()
    os.chdir(root)

    if is_shallow():
        print("⚠️  检测到浅克隆，提交历史可能不完整", file=sys.stderr)

    version = args.tag or read_version(root)
    build_number = os.environ.get("GITEE_PIPELINE_BUILD_NUMBER", "")

    changelog = root / args.changelog
    if args.from_ref:
        start, source = args.from_ref, "--from 指定"
        if not ref_exists(start):
            print(f"⚠️  起点 {start} 不存在，改用自动推断", file=sys.stderr)
            start, source = resolve_baseline(changelog, args.limit)
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
    )

    if args.output:
        out = Path(args.output)
        out.write_text(body, encoding="utf-8")
        print(f"==> 已写入 {out}", file=sys.stderr)
    else:
        print(body)

    if args.update_changelog:
        update_changelog(changelog, version, body, current_sha() or args.to_ref)
        print(f"==> 已更新 {changelog}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
