"""卸载数据去留向导测试

飞牛的卸载向导完全由 `wizard/uninstall` 驱动：文件缺失或结构非法时，应用中心的
卸载流程不会询问数据去留，用户的选择也不会传进 `cmd/uninstall_callback`。这类故障
在打包阶段是**静默的**（fnpack 甚至可能不报错），只能在真机卸载时才发现，因此在这里
锁定三件事：

  1. `wizard/uninstall` 是合法的步骤数组，控件类型与必填字段符合 fnpack 校验器要求。
  2. 数据去留是一个**显式的二选一**（radio），默认保留。
     —— 早期版本用的是默认关闭的 switch「删除全部本地数据」，语义上是「要不要删」
        而不是「是否保留」，用户容易看漏，也不符合「询问是否保留用户数据」的预期。
  3. `cmd/uninstall_callback` 读取的环境变量名与向导字段名一致，且默认分支是保留。
     —— 字段名即环境变量名（官方《用户向导》约定），改名会让用户选择静默失效；
        只有命中明确的清除取值才允许删数据，任何未预期取值一律保留。

另外锁定打包脚本对 fnpack 失败的处理：fnpack 校验失败时**退出码仍然是 0**，只在
stdout 打印 "Packing failed"，只靠 `set -e` / `||` 会把上一次的旧 FPK 当成新产物。
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

UNINSTALL_WIZARD = ROOT / "wizard" / "uninstall"
UNINSTALL_CALLBACK = ROOT / "cmd" / "uninstall_callback"
BUILD_SH = ROOT / "scripts" / "build_fpk.sh"
BUILD_BAT = ROOT / "scripts" / "build_fpk.bat"

# fnpack 支持的类型（与 scripts/fpk_selfcheck.py 保持一致）
VALID_TYPES = {"text", "password", "radio", "checkbox", "select", "switch", "tips"}

PURGE_FIELD = "wizard_purge_data"


def load_wizard() -> list:
    data = json.loads(UNINSTALL_WIZARD.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data, "wizard/uninstall 必须是非空 JSON 数组"
    return data


def all_items(data: list) -> list:
    items = []
    for step in data:
        items.extend(step.get("items") or [])
    return items


def find_field(data: list, field: str) -> dict | None:
    return next((i for i in all_items(data) if i.get("field") == field), None)


def test_wizard_steps_are_valid():
    """每个步骤都要有标题与非空 items，控件类型与必填字段符合 fnpack 要求"""
    for index, step in enumerate(load_wizard()):
        assert step.get("stepTitle"), f"第 {index + 1} 步缺少 stepTitle"
        assert step.get("items"), f"第 {index + 1} 步 items 为空"
        for item in step["items"]:
            kind = item.get("type")
            assert kind in VALID_TYPES, f"不支持的控件类型: {kind}"
            if kind == "tips":
                assert item.get("helpText"), "tips 缺少 helpText"
            else:
                assert item.get("field"), f"{kind} 缺少 field"
                assert item.get("label"), f"{kind} 缺少 label"
                if kind in ("radio", "checkbox", "select"):
                    assert item.get("options"), f"{kind} 缺少 options"


def test_data_choice_is_explicit_two_option_radio():
    """数据去留必须是二选一 radio，两个选项同时可见"""
    item = find_field(load_wizard(), PURGE_FIELD)
    assert item is not None, f"缺少数据去留字段 {PURGE_FIELD}"
    assert item["type"] == "radio", "数据去留应为 radio（两个选项同时可见），不是 switch"
    values = [opt.get("value") for opt in item["options"]]
    assert sorted(map(str, values)) == ["false", "true"], f"选项取值应为 false/true: {values}"


def test_default_choice_keeps_data():
    """默认值必须是保留，绝不能默认删除"""
    item = find_field(load_wizard(), PURGE_FIELD)
    assert item is not None, f"缺少数据去留字段 {PURGE_FIELD}"
    assert str(item.get("initValue")) == "false", "默认必须保留数据（initValue=false）"


def test_callback_reads_same_field_and_defaults_to_keep():
    """回调读取的环境变量名必须与向导字段名一致，且默认分支是保留"""
    script = UNINSTALL_CALLBACK.read_text(encoding="utf-8")
    assert PURGE_FIELD in script, f"{UNINSTALL_CALLBACK.name} 未读取 {PURGE_FIELD}"
    assert "${" + PURGE_FIELD + ":-false}" in script, "未取值时应回退为 false（保留）"
    # 只有命中明确的清除取值才允许出现删除动作
    body = script.split("case ", 1)[-1]
    purge_branch, _, keep_branch = body.partition("*)\n")
    assert "rm -rf" in purge_branch, "清除分支应执行删除"
    assert "rm -rf" not in keep_branch, "默认分支不得删除任何数据"


def test_callback_guards_pkgvar_before_delete():
    """TRIM_PKGVAR 为空或为 / 时必须跳过删除，防止误删系统目录"""
    script = UNINSTALL_CALLBACK.read_text(encoding="utf-8")
    assert "TRIM_PKGVAR" in script, "回调必须使用 TRIM_PKGVAR 而非硬编码路径"
    root_guard = '"${TRIM_PKGVAR}" = "/"'
    assert root_guard in script, "删除前必须排除 TRIM_PKGVAR 为 / 的情况，防止误删系统目录"


def test_build_scripts_gate_on_fnpack_failure():
    """fnpack 失败时退出码仍为 0，打包脚本必须按输出关键字判定"""
    for path in (BUILD_SH, BUILD_BAT):
        text = path.read_text(encoding="utf-8")
        assert "Packing failed" in text, f"{path.name} 未处理 fnpack 的 Packing failed（其退出码为 0）"
