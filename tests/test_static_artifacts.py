"""app/static 构建产物约定：把「产物不入库、测试不依赖、内容可再生」固化为测试

app/static 的定位（见 frontend/vite.config.js：outDir=../app/static 且
emptyOutDir=true，构建输入在 frontend/index.html 与 frontend/public/）：

- 整个目录都可由 frontend/ 构建再生；app/static/assets/（内容哈希命名的
  第三方压缩包）是纯构建产物，已被 .gitignore 排除，不入库
- 被跟踪的 index.html / sw.js / manifest.webmanifest / icons/ 是产物模板
  （sw.js 每次构建会被 vite 插件盖上时间戳，提交时带版本戳变化属正常）
- pytest 套件不得依赖 app/static：构建产物缺失或过期不应影响单元测试；
  安全扫描对 assets/ 内压缩包命中的 SSRF/注入类告警均为误报（浏览器端
  静态资源，不存在服务端执行），处置方式是说明而非改码
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
FRONTEND = ROOT / "frontend"

# 被跟踪的 static 模板文件 -> frontend/ 中的构建输入（可再生性映射）
TRACKED_TEMPLATE_SOURCES = {
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


def test_gitignore_excludes_built_assets():
    """app/static/assets（哈希产物）必须保持被 .gitignore 排除，防止产物入库"""
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    rules = [ln.strip() for ln in gitignore.splitlines()]
    assert any(rule.startswith("app/static/assets") for rule in rules), (
        ".gitignore 丢失 app/static/assets 排除规则，构建产物将被当作源码入库"
    )


def test_tracked_static_templates_have_frontend_sources():
    """被跟踪的 static 模板必须有 frontend/ 构建输入——static 内容全部可再生，
    不存在只能手改的孤本文件；新增跟踪文件时需同步登记其来源"""
    missing = [
        template
        for template, source in TRACKED_TEMPLATE_SOURCES.items()
        if not source.is_file()
    ]
    assert not missing, f"以下 static 模板缺少 frontend 构建输入：{missing}"


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
