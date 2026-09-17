"""Release 日志生成脚本（scripts/gen_release_notes.py）的单元测试

这个脚本是发布链路的**唯一日志产出源**——它的输出会直接出现在 Gitee Release
页面和 CHANGELOG.md 里，一旦出错用户立刻可见。因此以下逻辑必须有测试兜底：

  1. 提交解析正则 COMMIT_RE 的边界（无 type / 中文冒号 / 空 scope / 带数字）
  2. GROUP_ORDER 与 TYPE_ALIASES 的一致性，以及表外 type 的去向
  3. 破坏性变更只认标题的 ! 标记（不得对标题做 BREAKING CHANGE 子串匹配）
  4. 基线四级推断的优先级与降级
  5. CHANGELOG 同版本重复运行的幂等性

注意：这里全部用**进程内调用**，不执行 git 命令（涉及 Git 的部分只测纯函数）。
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "gen_release_notes.py"


def _load_module():
    """从文件路径加载脚本模块（scripts/ 不是包，无法直接 import）。"""
    spec = importlib.util.spec_from_file_location("gen_release_notes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_release_notes"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def grn():
    assert SCRIPT.is_file(), f"缺少脚本：{SCRIPT}"
    return _load_module()


def _commit(subject: str, short: str = "abc1234") -> dict:
    """构造一条提交记录（只填 group_commits 需要的字段）。"""
    return {
        "sha": short.ljust(40, "0"),
        "subject": subject,
        "author": "tester",
        "short": short,
    }


# ---------------------------------------------------------------- 正则边界


@pytest.mark.parametrize(
    "subject",
    [
        "feat(import): 差异报告支持导出 CSV",
        "fix: 修复白屏",
        "feat(api)!: 统一错误响应结构",
        "docs(spec)：中文冒号也可用",
        "chore(deps): 升级依赖",
    ],
)
def test_commit_re_accepts_valid(grn, subject):
    """合法提交标题应被正则接受"""
    assert grn.COMMIT_RE.match(subject) is not None


def test_commit_re_requires_halfwidth_parentheses(grn):
    """scope 必须用半角括号：全角括号（）不被识别，会落入其他变更

    这是正则的既定行为（`\\(` 只匹配半角），规范要求 scope 用英文小写写法。
    若哪天要支持全角，需同时放宽 COMMIT_RE 与钩子正则。
    """
    assert grn.COMMIT_RE.match("docs（spec）：全角括号") is None
    assert grn.COMMIT_RE.match("docs(spec): 半角括号") is not None


@pytest.mark.parametrize(
    "subject",
    [
        "update VERSION.",
        "新增发布者名称",
        "修改了几个文件",
        "wip",
        "Merge branch 'dev' into main",
    ],
)
def test_commit_re_rejects_invalid(grn, subject):
    """无 type 的提交标题应被正则拒绝（落入其他变更）"""
    assert grn.COMMIT_RE.match(subject) is None


def test_commit_re_type_is_letters_only(grn):
    """type 只允许字母——带数字的 type（如 chore2）无法匹配，会落入其他变更"""
    assert grn.COMMIT_RE.match("chore2: 带数字的 type") is None
    assert grn.COMMIT_RE.match("chore: 正常") is not None


def test_commit_re_allows_empty_scope(grn):
    """空 scope 在格式上放行（是否推荐是另一回事，正则不应因此误拦）"""
    match = grn.COMMIT_RE.match("feat(): 空 scope")
    assert match is not None
    assert match.group("scope") == ""


def test_commit_re_extracts_fields(grn):
    """type / scope / breaking / subject 四个字段都要能正确取出"""
    match = grn.COMMIT_RE.match("feat(api)!: 统一错误响应结构")
    assert match.group("type") == "feat"
    assert match.group("scope") == "api"
    assert match.group("breaking") == "!"
    assert match.group("subject") == "统一错误响应结构"


# ---------------------------------------------------------------- 归组


def test_type_aliases_cover_all_group_keys(grn):
    """每个标准 type 都必须有自己的分组，否则提交会凭空消失"""
    group_keys = {key for key, _ in grn.GROUP_ORDER}
    for target in set(grn.TYPE_ALIASES.values()):
        assert target in group_keys, f"别名目标 {target} 没有对应分组"


def test_group_order_matches_documented_sequence(grn):
    """分组顺序即 Release 日志的输出顺序，必须稳定"""
    keys = [key for key, _ in grn.GROUP_ORDER]
    assert keys == [
        "security",
        "feat",
        "fix",
        "perf",
        "refactor",
        "test",
        "ci",
        "build",
        "docs",
        "style",
        "chore",
        "revert",
    ]


def test_group_commits_by_type(grn):
    """各 type 应进入各自分组，且不重复"""
    commits = [
        _commit("feat(import): 导出 CSV", "aaaaaaa"),
        _commit("fix(api): 修复白屏", "bbbbbbb"),
        _commit("docs: 补充说明", "ccccccc"),
    ]
    buckets = grn.group_commits(commits)
    assert [c["short"] for c in buckets["feat"]] == ["aaaaaaa"]
    assert [c["short"] for c in buckets["fix"]] == ["bbbbbbb"]
    assert [c["short"] for c in buckets["docs"]] == ["ccccccc"]


def test_group_commits_alias_maps_to_standard(grn):
    """别名 type 应归入对应的标准分组"""
    buckets = grn.group_commits(
        [
            _commit("feature(x): 别名", "aaaaaaa"),
            _commit("hotfix: 热修", "bbbbbbb"),
            _commit("deps: 升级依赖", "ccccccc"),
        ]
    )
    assert len(buckets["feat"]) == 1
    assert len(buckets["fix"]) == 1
    assert len(buckets["chore"]) == 1


def test_group_commits_unknown_letters_type_goes_other(grn):
    """表外的纯字母 type 虽能通过正则，仍须落入「其他变更」"""
    buckets = grn.group_commits([_commit("xxx: 随便写", "aaaaaaa")])
    assert len(buckets[grn.OTHER_TITLE]) == 1
    for key, _ in grn.GROUP_ORDER:
        assert not buckets[key]


def test_group_commits_malformed_goes_other(grn):
    """不符合格式的提交不能被丢弃，必须进「其他变更」"""
    buckets = grn.group_commits([_commit("update VERSION.", "aaaaaaa")])
    assert len(buckets[grn.OTHER_TITLE]) == 1


def test_group_commits_never_drops_any_commit(grn):
    """所有提交都必须出现在某个分组里（绝不丢提交是这个脚本的核心承诺）"""
    subjects = [
        "feat: 正常新功能",
        "xxx: 表外 type",
        "update VERSION.",
        "feature: 别名",
        "Merge branch 'dev'",
    ]
    commits = [_commit(s, f"{i:07d}") for i, s in enumerate(subjects)]
    buckets = grn.group_commits(commits)
    total = sum(len(v) for v in buckets.values())
    # 破坏性变更组会与主分组重复计数，因此这里断言 >= 提交数
    assert total >= len(commits)
    collected = {c["short"] for v in buckets.values() for c in v}
    assert collected == {f"{i:07d}" for i in range(len(subjects))}


# ---------------------------------------------------------------- 破坏性变更


def test_breaking_flagged_by_bang_only(grn):
    """带 ! 的提交进入破坏性变更组，同时保留在原分组"""
    buckets = grn.group_commits([_commit("feat(api)!: 移除旧字段", "aaaaaaa")])
    assert len(buckets[grn.BREAKING_TITLE]) == 1
    assert len(buckets["feat"]) == 1


def test_breaking_not_triggered_by_substring(grn):
    """标题里出现 BREAKING CHANGE 字样不得被误判为破坏性变更

    这是曾经的缺陷：早期实现用 "BREAKING CHANGE" in subject 做子串判断，
    导致 `docs: 补充 BREAKING CHANGE 章节说明` 被错放进破坏性变更组并置顶。
    """
    buckets = grn.group_commits(
        [_commit("docs: 补充 BREAKING CHANGE 章节说明", "aaaaaaa")]
    )
    assert buckets[grn.BREAKING_TITLE] == []
    assert len(buckets["docs"]) == 1


def test_breaking_group_empty_when_no_bang(grn):
    """没有任何 ! 标记时，破坏性变更组应为空（render 会跳过空分组）"""
    buckets = grn.group_commits([_commit("feat: 普通新功能", "aaaaaaa")])
    assert buckets[grn.BREAKING_TITLE] == []


# ---------------------------------------------------------------- 渲染


def test_render_includes_group_titles_and_entries(grn):
    """渲染结果应含分组标题、条目标题与提交缩写"""
    body = grn.render(
        version="1.2.3",
        commits=[_commit("feat(import): 导出 CSV", "aaaaaaa")],
        range_desc="v1.2.2..HEAD",
    )
    assert "## fn-finstat v1.2.3" in body
    assert "✨ 新功能" in body
    assert "**import**: 导出 CSV" in body
    assert "(aaaaaaa)" in body


def test_render_includes_build_number_and_sha256(grn):
    """构建号与 SHA-256 存在时应写入正文"""
    body = grn.render(
        version="1.2.3",
        commits=[_commit("fix: 修复", "bbbbbbb")],
        range_desc="v1.2.2..HEAD",
        sha256="deadbeef" * 8,
        build_number="42",
    )
    assert "构建 #42" in body
    assert "deadbeef" * 8 in body
    assert "fn-finstat-v1.2.3.fpk" in body


def test_render_without_commits_still_valid(grn):
    """零提交时仍应产出可读文案，而不是崩溃或空文件"""
    body = grn.render(version="1.0.0", commits=[], range_desc="HEAD~30..HEAD")
    assert "## fn-finstat v1.0.0" in body
    assert "没有检测到新的提交记录" in body


def test_render_skips_empty_groups(grn):
    """没有内容的分组不应输出标题（避免日志里全是空小节）"""
    body = grn.render(
        version="1.0.0",
        commits=[_commit("fix: 只有修复", "aaaaaaa")],
        range_desc="HEAD~1..HEAD",
    )
    assert "🐛 问题修复" in body
    assert "✨ 新功能" not in body
    assert "🔒 安全修复" not in body


# ---------------------------------------------------------------- CHANGELOG


def test_changelog_is_idempotent_for_same_version(grn, tmp_path):
    """同一版本重复写 CHANGELOG 必须覆盖而非追加（幂等）"""
    changelog = tmp_path / "CHANGELOG.md"
    body = grn.render(
        version="2.0.0", commits=[_commit("feat: 首版", "aaaaaaa")], range_desc="x"
    )

    grn.update_changelog(changelog, "2.0.0", body, "a" * 40)
    first = changelog.read_text(encoding="utf-8")

    grn.update_changelog(changelog, "2.0.0", body, "a" * 40)
    second = changelog.read_text(encoding="utf-8")

    assert first == second, "同版本重复运行产生了差异，幂等性被破坏"
    assert second.count("## fn-finstat v2.0.0") == 1


def test_changelog_new_version_prepended(grn, tmp_path):
    """新版本应插到已有版本段之前（最新在上）"""
    changelog = tmp_path / "CHANGELOG.md"
    old = grn.render(
        version="1.0.0", commits=[_commit("feat: 旧版", "aaaaaaa")], range_desc="x"
    )
    new = grn.render(
        version="1.1.0", commits=[_commit("feat: 新版", "bbbbbbb")], range_desc="x"
    )

    grn.update_changelog(changelog, "1.0.0", old, "a" * 40)
    grn.update_changelog(changelog, "1.1.0", new, "b" * 40)
    text = changelog.read_text(encoding="utf-8")

    assert text.index("## fn-finstat v1.1.0") < text.index("## fn-finstat v1.0.0")


def test_changelog_writes_baseline_marker(grn, tmp_path):
    """每次写入都要留下 release-baseline 标记，供下次推断起点"""
    changelog = tmp_path / "CHANGELOG.md"
    body = grn.render(version="3.0.0", commits=[], range_desc="x")
    sha = "c" * 40
    grn.update_changelog(changelog, "3.0.0", body, sha)

    text = changelog.read_text(encoding="utf-8")
    assert f"<!-- release-baseline: {sha} -->" in text
    assert grn.BASELINE_RE.search(text).group("sha") == sha


def test_changelog_creates_file_with_header(grn, tmp_path):
    """文件不存在时应创建并写入说明性表头"""
    changelog = tmp_path / "CHANGELOG.md"
    body = grn.render(version="1.0.0", commits=[], range_desc="x")
    grn.update_changelog(changelog, "1.0.0", body, "d" * 40)

    text = changelog.read_text(encoding="utf-8")
    assert text.startswith("# 更新日志")
    assert "gen_release_notes.py" in text


# ---------------------------------------------------------------- 基线正则


def test_semver_tag_pattern_skips_build_number_tags(grn):
    """语义化 tag 正则必须跳过 v39 这类构建号 tag"""
    assert grn.SEMVER_TAG_RE.match("v1.2.3")
    assert grn.SEMVER_TAG_RE.match("v0.7.0")
    assert not grn.SEMVER_TAG_RE.match("v39")
    assert not grn.SEMVER_TAG_RE.match("v40")


def test_baseline_regex_requires_hex_sha(grn):
    """基线标记只认 6-40 位十六进制，防止误匹配正文里的其他注释"""
    assert grn.BASELINE_RE.search("<!-- release-baseline: 246d5d0 -->")
    assert grn.BASELINE_RE.search("<!-- release-baseline: " + "a" * 40 + " -->")
    assert not grn.BASELINE_RE.search("<!-- release-baseline: xyz -->")


# ---------------------------------------------------------------- 构建渠道


def test_semver_tag_pattern_skips_prerelease_tags(grn):
    """dev 流水线的预发布 tag 不能当作发版基线

    dev 分支每次推送都会打一个 v0.7.1-dev.N.ghash tag；若被当成基线，
    正式版日志的起点会落在最后一次测试构建上，中间合入 main 的变更消失。
    """
    assert not grn.SEMVER_TAG_RE.match("v0.7.1-dev.42.g1a2b3c4")
    assert not grn.SEMVER_TAG_RE.match("v0.7.1-rc.1")
    assert grn.SEMVER_TAG_RE.match("v0.7.1")


def test_dev_channel_marks_test_build(grn):
    """dev 渠道的说明必须显著标注测试版本

    Release 附件是可直接下载安装的，用户往往只看正文不看 tag，
    正文顶部不带警示就会把测试包当正式版装到设备上。
    """
    body = grn.render(
        version="0.7.1-dev.42.g1a2b3c4",
        commits=[_commit("feat(import): 新增分账导入")],
        range_desc="HEAD~30..HEAD",
        channel="dev",
    )

    assert "测试版本" in body
    assert body.startswith("## fn-finstat v0.7.1-dev.42.g1a2b3c4")
    # 安装说明里的附件名要跟着派生版本走，否则指向一个不存在的文件
    assert "fn-finstat-v0.7.1-dev.42.g1a2b3c4.fpk" in body


def test_release_channel_has_no_test_banner(grn):
    """正式版说明不得出现测试版声明（默认渠道即 release）"""
    body = grn.render(
        version="0.7.1",
        commits=[_commit("feat(import): 新增分账导入")],
        range_desc="HEAD~30..HEAD",
    )

    assert "测试版本" not in body
    assert "测试版本" not in grn.render(
        version="0.7.1",
        commits=[],
        range_desc="x",
        channel="release",
    )


# ---------------------------------------------------------------- 附件与校验指引


def test_render_points_to_md5_checksum_file(grn):
    """校验指引必须写进说明正文

    Release 附件里光有一个 MD5SUMS.txt，用户并不知道它是干什么用的、
    更不会知道该用 `md5sum -c` 去跑它——不说等于没发。
    """
    body = grn.render(version="0.7.1", commits=[], range_desc="x")

    assert "MD5SUMS.txt" in body
    assert "md5sum -c" in body


def test_render_mentions_channel_alias_when_given(grn):
    """给了渠道别名时，安装说明要同时指向稳定入口与本次构建产物"""
    body = grn.render(
        version="0.7.1",
        commits=[],
        range_desc="x",
        alias="fn-finstat-latest.fpk",
    )

    assert "fn-finstat-latest.fpk" in body
    assert "fn-finstat-v0.7.1.fpk" in body


def test_render_without_alias_falls_back_to_versioned_asset(grn):
    """没给别名（本地手动生成日志）时退化为只提带版本号副本，不留空占位"""
    body = grn.render(version="0.7.1", commits=[], range_desc="x")

    assert "fn-finstat-v0.7.1.fpk" in body
    assert "None" not in body
    assert "``" not in body
