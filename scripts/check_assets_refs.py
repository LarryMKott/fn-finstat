"""静态产物引用校验：确认 app/static/index.html 引用的每个本地资源真实存在且非空。

背景（见 docs/devlog/2026-09-13-全量代码评审报告.md 的 S-1）：
前端产物目录 app/static 不入库（.gitignore 排除整目录），由 vite 构建生成。
若 index.html 引用了缺失或不匹配的 JS/CSS，设备上打开应用会白屏 —— 这类
事故靠肉眼比对哈希文件名几乎不可能发现，故做成打包前的硬门禁。

校验范围（2026-09-14 扩展，见 2026-09-14 全量代码审查报告 M-5）：
- 全部本地 src/href 引用（不再局限于 assets/）：图标、manifest、sw.js 一并纳入
- 外链（http(s)://、//、data:、mailto:、tel:、#）跳过，不参与校验
- 每个文件必须存在**且非空** —— 0 字节的产物同样会让页面局部失效
- 额外检查 icons/ 目录存在且非空：PWA 图标缺失时应用图标变空白，但不会
  让页面报错，容易被引用检查漏掉，故单独兜底

用法：
    python scripts/check_assets_refs.py [index.html路径] [静态根目录]
缺省：index.html = app/static/index.html，静态根目录 = app/static
退出码：0 全部引用可解析；1 存在缺失或空文件（构建脚本据此中止打包）
"""

import re
import sys
from pathlib import Path

# 匹配 index.html 内的静态资源引用（src="..." / href="..."）
_ASSET_REF = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)

# 外链或非文件引用：不参与本地产物校验
_EXTERNAL_PREFIXES = ("http://", "https://", "//", "data:", "mailto:", "tel:", "#")


def referenced_assets(html: str) -> list[str]:
    """提取 index.html 中引用的本地资源路径（去重，保持出现顺序）"""
    seen: dict[str, None] = {}
    for raw in _ASSET_REF.findall(html):
        # 去掉查询串/锚点，统一分隔符，剥离 ./ 前缀
        path = raw.split("?")[0].split("#")[0].replace("\\", "/").strip()
        if not path or path.lower().startswith(_EXTERNAL_PREFIXES):
            continue
        if path.startswith("./"):
            path = path[2:]
        seen.setdefault(path, None)
    return list(seen)


def main() -> int:
    html_path = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else Path("app/static/index.html")
    )
    static_root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("app/static")

    if not html_path.is_file():
        print(f"错误：找不到入口文件 {html_path}", file=sys.stderr)
        return 1

    refs = referenced_assets(html_path.read_text(encoding="utf-8"))
    if not refs:
        print(
            "错误：index.html 未引用任何本地资源，疑似构建未完成或被覆盖",
            file=sys.stderr,
        )
        return 1

    missing: list[str] = []
    empty: list[str] = []
    for ref in refs:
        target = static_root / ref
        if not target.is_file():
            missing.append(ref)
        elif target.stat().st_size == 0:
            empty.append(ref)

    for ref in refs:
        flag = "缺失" if ref in missing else ("空文件" if ref in empty else "OK")
        print(f"  [{flag}] {ref}")

    problems: list[str] = []
    if missing:
        problems.append(f"{len(missing)} 个引用不存在（{', '.join(missing)}）")
    if empty:
        problems.append(f"{len(empty)} 个产物为 0 字节（{', '.join(empty)}）")

    # 图标目录兜底：缺失时图标变空白但不报错，单独检查以免被引用变化漏掉
    icons_dir = static_root / "icons"
    if not icons_dir.is_dir() or not any(icons_dir.iterdir()):
        problems.append(f"图标目录缺失或为空（{icons_dir}）")
    else:
        print(f"  [OK] icons/ 共 {len(list(icons_dir.iterdir()))} 个文件")

    if problems:
        print(
            "错误：" + "；".join(problems) + "。设备端会出现白屏或图标缺失，"
            "请重新构建前端：cd frontend && npm ci && npm run build",
            file=sys.stderr,
        )
        return 1

    print(f"静态产物引用校验通过：{len(refs)} 个本地资源全部存在且非空")
    return 0


if __name__ == "__main__":
    sys.exit(main())
