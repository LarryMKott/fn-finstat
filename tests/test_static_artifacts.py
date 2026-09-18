"""app/static 构建产物约定：把「整目录不入库、测试不依赖、内容可再生」固化为测试

app/static 的定位（见 frontend/vite.config.js：outDir=../app/static 且
emptyOutDir=true，构建输入在 frontend/index.html 与 frontend/public/）：

- 整个目录都是 vite 构建产物，**没有任何文件入库**。构建会先 emptyOutDir 清空
  目录再重写，因此历史上"index.html / sw.js / manifest / icons 是输入模板、必须
  入库"的说法是错的：它们一样会被删掉重写，其中 sw.js 每次构建还会被
  bump-sw-version 插件盖上新时间戳，入库只会导致工作区持续 dirty。
- app/static/assets/ 是内容哈希命名的 js/css 产物，同样不入库。
- pytest 套件不得依赖 app/static：构建产物缺失或过期不应影响单元测试；
  安全扫描对 assets/ 内压缩包命中的 SSRF/注入类告警均为误报（浏览器端
  静态资源，不存在服务端执行），处置方式是说明而非改码。
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
FRONTEND = ROOT / "frontend"

# 构建产物 -> 它在 frontend/ 里的唯一来源（可再生性映射）。
# 用途：确保 app/static 里的东西全都有出处，不存在只能手改的孤本文件。
BUILD_INPUTS = {
    "index.html": FRONTEND / "index.html",
    "sw.js": FRONTEND / "public" / "sw.js",
    "manifest.webmanifest": FRONTEND / "public" / "manifest.webmanifest",
    "icons/icon-light-64.png": FRONTEND / "public" / "icons" / "icon-light-64.png",
    "icons/icon-light-256.png": FRONTEND / "public" / "icons" / "icon-light-256.png",
    "icons/icon-dark-64.png": FRONTEND / "public" / "icons" / "icon-dark-64.png",
    "icons/icon-dark-256.png": FRONTEND / "public" / "icons" / "icon-dark-256.png",
}

# vite 内容哈希产物命名：name-HASH.js/css（HASH 为 base64url 风格 8+ 位）
_ASSET_HASHED = re.compile(r"^[\w-]+-[\w-]{8,}\.(?:js|css)$")


def _gitignore_rules() -> list[str]:
    return [
        ln.strip()
        for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    ]


def test_gitignore_excludes_entire_static_dir():
    """app/static 整目录必须被 .gitignore 排除，防止任何构建产物入库

    只排除 assets/ 是不够的：index.html / sw.js / manifest / icons 同样是
    vite 构建输出（emptyOutDir=true 会先删再写），入库后每次 build 都会
    让工作区 dirty，且会留下「index.html 引用了不存在的 assets」的半套产物。
    """
    rules = _gitignore_rules()
    assert any(
        rule.startswith("app/static/") for rule in rules
    ), ".gitignore 缺少整目录排除规则 app/static/，构建产物将被当作源码入库"


def test_no_static_file_is_tracked():
    """git 索引中不得存在任何 app/static 文件

    只看 .gitignore 不够——已经被跟踪的文件不受 ignore 规则约束，必须
    显式确认索引是干净的。用 git 而非工作区判断：工作区里本来就该有产物。
    """
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "app/static"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("无法执行 git ls-files（非 git 工作区）")
    assert not tracked, (
        "以下构建产物仍被 git 跟踪，请执行 git rm -r --cached app/static：" f"{tracked}"
    )


def test_build_inputs_exist():
    """app/static 的每个产物都必须在 frontend/ 有构建输入——不存在孤本文件"""
    missing = [name for name, source in BUILD_INPUTS.items() if not source.is_file()]
    assert not missing, f"以下产物缺少 frontend 构建输入：{missing}"


def test_assets_directory_contains_only_hashed_bundles():
    """assets/ 内只允许内容哈希命名的 vite 产物；出现其他文件即说明有
    手工文件混入构建输出目录（应放 frontend/public/ 或另行入库）"""
    assets = STATIC / "assets"
    if not assets.is_dir():
        pytest.skip("app/static/assets 不存在（尚未构建前端），无可校验产物")
    unexpected = [
        f.name
        for f in assets.iterdir()
        if f.is_file() and not _ASSET_HASHED.match(f.name)
    ]
    assert not unexpected, f"assets/ 出现非哈希命名的文件：{unexpected}"


def test_tests_do_not_depend_on_static():
    """测试套件不得引用 app/static（构建产物缺失/过期不影响单元测试）。
    本文件自身的 docstring 提及 static 属说明文字，排除在校验外。"""
    offenders = []
    for py in Path(__file__).resolve().parent.glob("*.py"):
        if py.name == Path(__file__).name:
            continue
        text = py.read_text(encoding="utf-8")
        if "app/static" in text or "app.static" in text or '"static"' in text:
            offenders.append(py.name)
    assert not offenders, f"以下测试文件依赖了构建产物目录 app/static：{offenders}"
