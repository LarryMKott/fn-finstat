"""核对前后端主题归一化取值域一致

前端 fnos.js 的 normalizeTheme 与后端 deps._normalize_theme 是两处独立实现
（内联脚本无法复用模块，后端也无法复用 JS），必须保证对同一输入给出同一结论，
否则会出现「后端说夜间、前端判日间」的撕裂。

用法：python tests/verify_theme_parity.py
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.api.deps import normalize_theme  # noqa: E402

# 覆盖各种可能写法：飞牛数字码、单词、首字母大小写、空白、布尔、无法识别值
SAMPLES = [
    "10",
    "20",
    "1",
    "2",
    "light",
    "dark",
    "day",
    "night",
    "LIGHT",
    "Dark",
    "DAY",
    " Night ",
    "true",
    "false",
    " 20 ",
    "10 ",
    "auto",
    "system",
    "",
    "   ",
    "yes",
    "0",
]

JS_DRIVER = r"""
// 以脚本文件方式运行时 node 的 argv 布局是：
//   [0] node.exe  [1] 本脚本路径  [2] 目标源文件  [3] 样本 JSON
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const modeDecl = src.match(/const FNOS_MODE = \{[^}]*\};/)[0];
const fnDecl = src.match(/function normalizeTheme\(raw\) \{[\s\S]*?\n\}/)[0];
// 抽出的片段落盘为临时模块再 require：避免 eval 动态求值（静态扫描按代码注入拦截）
const scratch = process.argv[1] + '.extracted.cjs';
fs.writeFileSync(
  scratch,
  modeDecl + '\n' + fnDecl + '\nmodule.exports = { fn: normalizeTheme };\n',
);
const box = require(scratch);
fs.rmSync(scratch, { force: true });
const samples = JSON.parse(process.argv[3]);
const out = {};
for (const s of samples) { const r = box.fn(s); out[s] = r === null ? '' : r; }
process.stdout.write(JSON.stringify(out));
"""


def main() -> int:
    frontend_src = ROOT / "frontend" / "src" / "fnos.js"
    if not frontend_src.exists():
        print(f"缺少前端源文件：{frontend_src}")
        return 1

    driver = ROOT / ".theme_parity_driver.cjs"
    driver.write_text(JS_DRIVER, encoding="utf-8")
    try:
        node = "C:/Users/WWTAW/.workbuddy/binaries/node/versions/22.22.2-3/node.exe"
        proc = subprocess.run(
            [node, str(driver), str(frontend_src), json.dumps(SAMPLES)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if proc.returncode != 0:
            print("前端驱动执行失败：")
            print(proc.stderr)
            return 1
        fe_map = json.loads(proc.stdout)
    finally:
        driver.unlink(missing_ok=True)
        Path(str(driver) + ".extracted.cjs").unlink(missing_ok=True)

    failures = 0
    print(f"{'输入':>12}  {'前端':<9}  {'后端':<9}  结果")
    print("-" * 46)
    for s in SAMPLES:
        fe = fe_map.get(s, "")
        be = normalize_theme(s)
        ok = fe == be
        if not ok:
            failures += 1
        print(
            f"{json.dumps(s):>12}  {fe!r:<9}  {be!r:<9}  {'一致' if ok else '★不一致'}"
        )

    print("-" * 46)
    print(f"{len(SAMPLES) - failures}/{len(SAMPLES)} 输入前后端结论一致")
    if failures:
        print(
            f"\n存在 {failures} 处不一致，需修正 frontend/src/fnos.js 或 app/api/deps.py"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
