"""前端统一加载层（useLoading / AppLoading）静态核对

Node 侧的 tests/loading.test.mjs 负责验证运行时行为（四态、防重入、不卡死），
这里负责守住两条容易在后续迭代中被破坏的静态约束：

  1. 凡是要发网络请求的组件，必须走统一的 runTask 通道（不能有遗漏的加载）；
  2. 浮层组件不得硬编码颜色，只能引用 --color-* 语义令牌（与项目设计系统约定一致）。

用法：pytest tests/test_frontend_loading.py
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"
STYLE = SRC / "assets" / "style.css"

# 这些模块自己也 import api/，但它们是被调用的 API 封装层，不是请求发起方
API_LAYER = (SRC / "api",)


def _read(path: Path) -> str:
    assert path.is_file(), f"缺少文件：{path}"
    return path.read_text(encoding="utf-8")


def _scan_vue_files():
    return sorted(SRC.rglob("*.vue")) + [SRC / "store.js"]


def _imports_api(text: str) -> bool:
    return bool(re.search(r'from\s+"[^"]*api/', text))


def _extract_run_task_blocks(text: str):
    """按大括号配对取出每一个 runTask({...}) 的配置对象字面量"""
    blocks = []
    for m in re.finditer(r"runTask\(\s*\{", text):
        start = m.end() - 1  # 指向 '{'
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(text[start : i + 1])
                    break
    return blocks


def test_app_mounts_loading_overlay():
    """AppLoading 必须在应用外壳挂载一次，否则所有 runTask 都没有可见反馈"""
    app = _read(SRC / "App.vue")
    assert 'import AppLoading from "./components/AppLoading.vue"' in app
    assert re.search(r"<AppLoading\s*/>", app), "App.vue 模板中未挂载 <AppLoading />"


def test_loading_composable_exports():
    src = _read(SRC / "composables" / "useLoading.js")
    for name in ("runTask", "isBusy", "loadingState", "dismissLoading", "TASK_CANCELLED"):
        assert re.search(rf"export (function|const) {name}\b", src), f"缺少导出 {name}"


def test_z_index_token_declared():
    css = _read(STYLE)
    assert "--z-hud:" in css, "style.css 未声明 --z-hud 层级令牌"
    assert "--z-modal: 100;" in css and "--z-toast: 200;" in css


def test_every_api_consumer_uses_run_task():
    """发请求的组件必须接入统一通道，避免出现没有任何加载反馈的死等"""
    missing = []
    for path in _scan_vue_files():
        text = _read(path)
        if not _imports_api(text):
            continue
        if "runTask" not in text:
            missing.append(str(path.relative_to(ROOT)))
    assert not missing, f"以下文件发起了 API 请求但没有使用 runTask：\n" + "\n".join(missing)


def test_every_run_task_has_key_and_title():
    """key 是防重入的粒度，title 是浮层上的任务名，两者缺一不可"""
    problems = []
    for path in _scan_vue_files():
        text = _read(path)
        for block in _extract_run_task_blocks(text):
            # 支持 `key: "x"` 与对象简写 `key,` 两种写法（BillsPanel 走的是简写）
            if not re.search(r"(?m)^\s*key\s*[,:]", block):
                problems.append(f"{path.relative_to(ROOT)}: runTask 缺少 key")
            if not re.search(r"(?m)^\s*title\s*[,:]", block):
                problems.append(f"{path.relative_to(ROOT)}: runTask 缺少 title")
    assert not problems, "\n".join(problems)


def test_loading_overlay_uses_design_tokens_only():
    """浮层配色必须走 --color-* 语义层，禁止硬编码色值（设计系统硬规则）"""
    vue = _read(SRC / "components" / "AppLoading.vue")
    style_part = vue.split("<style", 1)[-1]
    hardcoded = re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(", style_part)
    assert not hardcoded, f"AppLoading.vue 出现硬编码色值：{set(hardcoded)}"


def test_no_leftover_hand_rolled_busy_flags():
    """旧的散落 busy/loading ref 应已迁移到 isBusy(key)，防止两套状态各说各话"""
    offenders = []
    for path in _scan_vue_files():
        text = _read(path)
        for m in re.finditer(r"const\s+(\w*(?:[Bb]usy|[Ll]oading)\w*)\s*=\s*ref\(", text):
            offenders.append(f"{path.relative_to(ROOT)}: {m.group(1)}")
    assert not offenders, "仍存在手写的 busy/loading ref：\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    "rel",
    [
        "frontend/src/composables/useLoading.js",
        "frontend/src/components/AppLoading.vue",
        "frontend/tests/loading.test.mjs",
    ],
)
def test_loading_files_exist(rel):
    assert (ROOT / rel).is_file(), f"缺少 {rel}"


def test_npm_test_script_registered():
    pkg = _read(ROOT / "frontend" / "package.json")
    assert '"test": "node tests/loading.test.mjs"' in pkg
