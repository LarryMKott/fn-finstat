"""核对 docs/README.md 索引中「最后更新」列与文件实际修改时间是否一致。

背景：索引的「最后更新」列长期靠人工维护，容易与实际 mtime 漂移
（见 docs/README.md §6 待处理事项）。本脚本把它变成可随时核对的机械检查。

用法：
    python scripts/check_docs_index.py

退出码：
    0 = 无漂移；1 = 存在漂移（或索引登记了不存在的文件）

说明：
- 只读取，不修改任何文件；修正请手工改索引表格。
- 索引里的路径写法有两种：仓库根相对（`README.md`、`docs/xxx.md`）与
  docs/ 目录相对（`开发规范/xxx.md`、`2026-xx-xx-xxx.md`），脚本两者都认。
- 如果发现某条目的 mtime 看着像批量 touch（多个文件同一时刻），
  用 `git log -1 --format=%ad --date=short -- <文件>` 交叉确认真实改动日，
  以提交日为准，别把噪音写进索引。
"""

from __future__ import annotations

import datetime
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(REPO_ROOT, "docs", "README.md")

# 匹配「| `路径` | 用途 | 状态 | YYYY-MM-DD |」这类四列表格行
ROW = re.compile(
    r"^\|\s*`([^`]+)`\s*\|(.+)\|\s*([^|]+?)\s*\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*$"
)


def resolve(rel: str) -> str | None:
    """索引路径 -> 实际文件；找不到返回 None。"""
    for cand in (os.path.join(REPO_ROOT, rel), os.path.join(REPO_ROOT, "docs", rel)):
        if os.path.exists(cand):
            return cand
    return None


def main() -> int:
    text = open(INDEX, encoding="utf-8").read()
    rows = []
    for line in text.splitlines():
        m = ROW.match(line.strip())
        if m:
            rows.append((m.group(1), m.group(4)))

    print(f"索引中带「最后更新」的条目 = {len(rows)}")
    print()

    drift: list[tuple[str, str, str]] = []
    missing: list[str] = []

    for rel, declared in rows:
        path = resolve(rel)
        if path is None:
            missing.append(rel)
            print(f"MISS {rel}")
            continue
        actual = datetime.datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
        same = actual == declared
        if not same:
            drift.append((rel, declared, actual))
        print(f"{'OK  ' if same else 'DIFF'} {rel:48} 索引={declared}  实际={actual}")

    print()
    if missing:
        print("索引登记但文件不存在：")
        for rel in missing:
            print("   ", rel)
    if drift:
        print(f"漂移条目 = {len(drift)}（建议把索引日期改成实际值）：")
        for rel, declared, actual in drift:
            print(f"   {rel}: {declared} -> {actual}")
    else:
        print("无漂移。")

    return 1 if (drift or missing) else 0


if __name__ == "__main__":
    sys.exit(main())
