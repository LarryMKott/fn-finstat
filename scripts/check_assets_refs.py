"""静态产物引用校验：确认 app/static/index.html 引用的每个 assets/* 文件真实存在。

背景（见 docs/devlog/2026-09-13-全量代码评审报告.md 的 S-1）：
前端产物目录 app/static/assets 不入库（.gitignore 排除），由 vite 构建生成。
若 index.html 引用了缺失或不匹配的 JS/CSS，设备上打开应用会白屏 —— 这类
事故靠肉眼比对哈希文件名几乎不可能发现，故做成打包前的硬门禁。

用法：
    python scripts/check_assets_refs.py [index.html路径] [静态根目录]
缺省：index.html = app/static/index.html，静态根目录 = app/static
退出码：0 全部引用可解析；1 存在缺失引用（构建脚本据此中止打包）
"""

import re
import sys
from pathlib import Path

# 匹配 index.html 内的静态资源引用（src="..." / href="..."），仅取 assets/ 下的
_ASSET_REF = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def referenced_assets(html: str) -> list[str]:
    """提取 index.html 中引用的 assets/* 路径（去重，保持出现顺序）"""
    seen: dict[str, None] = {}
    for raw in _ASSET_REF.findall(html):
        # 去掉查询串/锚点，统一分隔符，剥离 ./ 前缀
        path = raw.split("?")[0].split("#")[0].replace("\\", "/")
        path = path.lstrip("./")
        if "/assets/" in path or path.startswith("assets/"):
            seen.setdefault(path, None)
    return list(seen)


def main() -> int:
    html_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("app/static/index.html")
    static_root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("app/static")

    if not html_path.is_file():
        print(f"错误：找不到入口文件 {html_path}", file=sys.stderr)
        return 1

    refs = referenced_assets(html_path.read_text(encoding="utf-8"))
    if not refs:
        print("错误：index.html 未引用任何 assets/ 资源，疑似构建未完成或被覆盖", file=sys.stderr)
        return 1

    missing = [ref for ref in refs if not (static_root / ref).is_file()]
    for ref in refs:
        flag = "缺失" if ref in missing else "OK"
        print(f"  [{flag}] {ref}")

    if missing:
        print(
            f"错误：index.html 引用了 {len(missing)} 个不存在的产物文件，"
            f"设备端会白屏。请重新构建前端：cd frontend && npm ci && npm run build",
            file=sys.stderr,
        )
        return 1

    print(f"静态产物引用校验通过：{len(refs)} 个资源全部存在")
    return 0


if __name__ == "__main__":
    sys.exit(main())
