"""fpk_selfcheck.source_paths 的单元测试。

回归背景：期望清单曾与打包脚本同源硬编码（main.py + config.py），上游新增
顶层模块 app/file_settings.py 后 fpk 缺文件、设备上 import 即
ModuleNotFoundError 启动失败，自检因两边清单一致而漏报。现在顶层模块
从源码目录动态推导，本测试钉住这一行为：新增顶层模块必须自动进入期望集。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import fpk_selfcheck  # noqa: E402

REPO_APP = Path(__file__).resolve().parents[2] / "app"


def test_every_toplevel_module_is_expected():
    """源码 app/ 下每个顶层 .py 都必须出现在期望清单里（漏打包会被自检拦截）"""
    expected = fpk_selfcheck.source_paths(REPO_APP)
    for py in REPO_APP.glob("*.py"):
        assert f"app/{py.name}" in expected, py.name


def test_real_repo_expected_set_covers_current_modules():
    """当前仓库的三个顶层模块（main/config/file_settings）都在期望集中"""
    expected = fpk_selfcheck.source_paths(REPO_APP)
    for name in ("app/main.py", "app/config.py", "app/file_settings.py"):
        assert name in expected, name


def test_new_toplevel_module_enters_expected_set(tmp_path):
    """源码目录新增顶层模块后，期望集自动包含它（回归核心）"""
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "brand_new_module.py").write_text("", encoding="utf-8")
    expected = fpk_selfcheck.source_paths(tmp_path)
    assert "app/main.py" in expected
    assert "app/brand_new_module.py" in expected
