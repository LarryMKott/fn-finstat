"""配色主题静态核对

主题体系有两个正交维度：明暗模式（auto/light/dark → html.dark）与配色主题
（html[data-theme]，松烟/沧蓝/紫棠/绯樱/焦糖/石墨）。配色定义在
frontend/src/assets/styles/themes.css，每个主题必须日间+夜间成对出现——
只写日间套会在夜间模式下被基础 html.dark 令牌「抢回」，主题形同虚设。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "frontend" / "src"
THEMES_CSS = SRC / "assets" / "styles" / "themes.css"
THEME_JS = SRC / "theme.js"


def _declared_accents() -> list[str]:
    """theme.js 导出的 ACCENTS 取值列表（UI 与主题样式的单一来源）"""
    js = THEME_JS.read_text(encoding="utf-8")
    m = re.search(r"export const ACCENTS = \[(.*?)\];", js, re.S)
    assert m, "theme.js 缺少 ACCENTS 导出"
    values = re.findall(r'value:\s*"(\w+)"', m.group(1))
    assert values, "ACCENTS 中未解析到任何主题取值"
    return values


def test_default_theme_pine_present_and_first():
    """松烟是默认主题：必须在列表首位（无 data-theme 属性即生效）"""
    accents = _declared_accents()
    assert accents[0] == "pine"
    # 默认主题不设 data-theme 属性，themes.css 不应有 pine 的覆盖块
    css = THEMES_CSS.read_text(encoding="utf-8")
    assert 'data-theme="pine"' not in css


def test_every_accent_has_light_and_dark_variants():
    """每个配色主题都必须日间 + 夜间成对定义，且两套都声明 --color-primary"""
    css = THEMES_CSS.read_text(encoding="utf-8")
    missing = []
    for accent in _declared_accents():
        if accent == "pine":
            continue
        light = f'html[data-theme="{accent}"]:not(.dark)'
        dark = f'html[data-theme="{accent}"].dark'
        for selector in (light, dark):
            if selector not in css:
                missing.append(f"{accent}: 缺少 {selector}")
                continue
            # 截取该选择器到下一个块之间，确认主色令牌存在
            tail = css.split(selector, 1)[1]
            block = re.split(r"\nhtml\[|\n:root", tail, 1)[0]
            if "--color-primary:" not in block:
                missing.append(f"{selector}: 未声明 --color-primary")
    assert not missing, "\n".join(missing)


def test_themes_imported_in_entry():
    """themes.css 必须在聚合入口中紧跟 tokens.css（主题块要整体接管基础令牌）"""
    entry = (SRC / "assets" / "style.css").read_text(encoding="utf-8")
    assert entry.index("styles/tokens.css") < entry.index("styles/themes.css")
    assert entry.index("styles/themes.css") < entry.index("styles/base.css")
