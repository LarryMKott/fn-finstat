"""FPK 打包自检：比对源码与 app.tgz 的文件清单（防漏打包文件），
校验外层 tgz 的关键条目（cmd 生命周期脚本、config、wizard）齐全且向导 JSON 合法，
校验设备布局关键路径（requirements.txt、ui/ 在根，Python 包在 app/ 子目录），
以及 index.html 引用的静态产物是否真实存在（防白屏）。

仅用标准库，build_fpk.sh 与 build_fpk.bat 共用。用法：
    python scripts/fpk_selfcheck.py [fpk路径] [源码app目录]
缺省：fpk=当前目录 fn-finstat.fpk，源码目录=当前目录 app/
"""

import io
import json
import sys
import tarfile
from pathlib import Path

# 与打包脚本的暂存规则保持一致：顶层 .py 模块全量入包（build_fpk.sh 的
# cp app/*.py / build_fpk.bat 的 for app\*.py），子目录仅枚举入包的这些。
# 顶层清单刻意从源码目录动态推导而非硬编码：曾写死 main/config 两文件，
# 上游新增顶层模块 file_settings.py 时三处清单均未更新，fpk 缺文件、
# 设备上 import 即 ModuleNotFoundError 启动失败，自检却因两边清单
# 同源而漏报。动态推导后新增顶层模块自动进入期望集，漏打包立即拦截。
PKG_DIRS = [
    "api",
    "core",
    "db",
    "parsers",
    "schemas",
    "services",
    "utils",
    "static",
    "ui",
]

# 外层 tgz（即设备上 /var/apps/{appname}/）必须存在的条目。
# 缺任何一项都会在真机上表现为「功能静默消失」：例如 wizard/uninstall 缺失时
# 飞牛卸载流程不会询问数据去留，cmd/uninstall_callback 缺失时用户选择不生效。
REQUIRED_OUTER = [
    "manifest",
    "ICON.PNG",
    "ICON_256.PNG",
    "cmd/main",
    "cmd/install_init",
    "cmd/install_callback",
    "cmd/uninstall_init",
    "cmd/uninstall_callback",
    "cmd/upgrade_init",
    "cmd/upgrade_callback",
    "cmd/config_init",
    "cmd/config_callback",
    "config/privilege",
    "config/resource",
    "wizard/install",
    "wizard/uninstall",
]

# fnpack 支持的向导控件类型（见 fnpack 校验器与官方文档《用户向导》）
VALID_WIZARD_TYPES = {
    "text",
    "password",
    "radio",
    "checkbox",
    "select",
    "switch",
    "tips",
}


def check_asset_refs(src_root: Path) -> None:
    """校验 index.html 引用的每个 assets/* 均存在（复用 check_assets_refs，防白屏）

    app/static 整目录不入库，若构建缺失或 index.html 与产物不匹配，打出的 FPK 会白屏；
    本检查在打包末尾兜底拦截。
    """
    from check_assets_refs import referenced_assets

    html = src_root / "static" / "index.html"
    if not html.is_file():
        raise AssertionError(f"缺少静态入口文件: {html}")
    static_root = src_root / "static"
    refs = referenced_assets(html.read_text(encoding="utf-8"))
    assert refs, "index.html 未引用任何 assets/ 资源，疑似构建未完成"
    missing = [r for r in refs if not (static_root / r).is_file()]
    assert not missing, f"index.html 引用了不存在的产物（会白屏）: {missing}"


def source_paths(src_root: Path) -> set[str]:
    expected = {"requirements.txt"}  # 打包时置于 app.tgz 根目录
    for f in src_root.glob("*.py"):  # 顶层模块动态枚举，见文件头注释
        expected.add(f"app/{f.name}")
    for d in PKG_DIRS:
        for f in (src_root / d).rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                rel = f.relative_to(src_root).as_posix()
                # ui/ 打包时置于 tgz 根（桌面入口目录），其余在 app/ 子目录
                expected.add(rel if rel.startswith("ui/") else f"app/{rel}")
    return expected


def check_outer(outer: tarfile.TarFile) -> None:
    """校验外层 tgz 的关键条目齐全，且 wizard JSON 结构合法

    外层 tgz 直接解包到 /var/apps/{appname}/，缺失项是「运行时才暴露」的静默故障：
    wizard/uninstall 不在包里时卸载流程不会询问数据去留，cmd 脚本缺失时生命周期
    回调不执行。这里在打包末尾兜底拦截。
    """
    names = {n for n in outer.getnames() if not n.endswith("/")}
    missing = [n for n in REQUIRED_OUTER if n not in names]
    assert not missing, f"外层包缺少关键条目: {missing}"


def check_wizard(outer: tarfile.TarFile, name: str) -> None:
    """校验 wizard 文件是合法的步骤数组，控件类型与必填字段符合 fnpack 校验器要求"""
    member = outer.extractfile(name)
    assert member is not None, f"{name} 无法读取"
    data = json.loads(member.read().decode("utf-8"))
    assert isinstance(data, list) and data, f"{name} 必须是非空 JSON 数组"
    for index, step in enumerate(data):
        assert isinstance(step, dict), f"{name} 第 {index + 1} 步不是对象"
        assert step.get("stepTitle"), f"{name} 第 {index + 1} 步缺少 stepTitle"
        items = step.get("items")
        assert isinstance(items, list) and items, f"{name} 第 {index + 1} 步 items 为空"
        for item in items:
            kind = item.get("type")
            assert kind in VALID_WIZARD_TYPES, f"{name} 出现未知控件类型: {kind}"
            if kind == "tips":
                assert item.get("helpText"), f"{name} 的 tips 缺少 helpText"
            else:
                assert item.get("field"), f"{name} 的 {kind} 缺少 field"
                assert item.get("label"), f"{name} 的 {kind} 缺少 label"
                if kind in ("radio", "checkbox", "select"):
                    assert item.get("options"), f"{name} 的 {kind} 缺少 options"


def main() -> None:
    fpk = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("fn-finstat.fpk")
    src_root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("app")

    with tarfile.open(fpk, "r:gz") as outer:
        # 外层：生命周期脚本、权限声明与向导必须齐全（缺失会导致功能静默消失）
        check_outer(outer)
        check_wizard(outer, "wizard/install")
        check_wizard(outer, "wizard/uninstall")
        if "wizard/config" in outer.getnames():
            check_wizard(outer, "wizard/config")

        inner = outer.extractfile("app.tgz")
        assert inner is not None, "fpk 中缺少 app.tgz"
        with tarfile.open(fileobj=io.BytesIO(inner.read()), mode="r:gz") as app_tgz:
            packed = {n for n in app_tgz.getnames() if not n.endswith("/")}

    missing = source_paths(src_root) - packed
    assert not missing, f"漏打包文件: {sorted(missing)}"
    assert "requirements.txt" in packed, "requirements.txt 未在 app.tgz 根目录"
    assert "app/main.py" in packed, "app/main.py 未在 app.tgz 包目录"
    assert "ui/config" in packed, "ui/config 未在 app.tgz 中"
    # index.html 引用的产物必须真实存在（assets 不入库，防构建缺失导致白屏）
    check_asset_refs(src_root)
    print(
        "打包自检通过：外层条目齐全，向导结构合法，源码文件全部入包，"
        "设备布局正确，静态产物引用完整"
    )


if __name__ == "__main__":
    main()
