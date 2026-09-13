"""官方 SDK 集成行为核对

frontend/src/fnos.js 接入飞牛官方 JS SDK（@trimjs/web-app）后，有三条不能靠肉眼
保证的行为契约，用本脚本锁定：

  1. SDK 只在 iframe 内尝试：独立浏览器页面（window.parent === window）必须跳过，
     否则会抛 "Host bridge is not available outside iframe or app runtime"
  2. SDK 调用必须带超时：iframe 内宿主不响应握手时 getPlatformConfig() 会永久挂起
     （实测 3s 无结果），withTimeout 必须能脱身并走 fallback
  3. SDK 推送的主题值（官方为 'light' | 'dark'）必须被 normalizeTheme 正确接受

做法：把 fnos.js 里这几个纯函数抽出来在 Node 里跑（不引入 Vue），逐条断言。
用法：./app/venv/Scripts/python.exe tests/verify_sdk_integration.py
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = "C:/Users/WWTAW/.workbuddy/binaries/node/versions/22.22.2-3/node.exe"

DRIVER = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');

// 抽出待验证的纯函数（不依赖 Vue / DOM 的模块顶层逻辑）
function extract(pattern, label) {
  const m = src.match(pattern);
  if (!m) throw new Error('未能从 fnos.js 抽出 ' + label);
  return m[0];
}

const parts = [
  extract(/const FNOS_MODE = \{[^}]*\};/, 'FNOS_MODE'),
  extract(/function normalizeTheme\(raw\) \{[\s\S]*?\n\}/, 'normalizeTheme'),
  extract(/function inIframe\(\) \{[\s\S]*?\n\}/, 'inIframe'),
  extract(/function withTimeout\(promise, ms, fallback\) \{[\s\S]*?\n\}\n/, 'withTimeout'),
];

const box = {};
eval(parts.join('\n') + '\nbox.normalizeTheme = normalizeTheme;'
   + '\nbox.inIframe = inIframe;'
   + '\nbox.withTimeout = withTimeout;');

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok, detail });
}

(async () => {
  // ---- 契约 1：SDK 主题值被正确归一化 ----
  check("normalizeTheme('dark')", box.normalizeTheme('dark') === 'dark');
  check("normalizeTheme('light')", box.normalizeTheme('light') === 'light');
  check("normalizeTheme('DARK')", box.normalizeTheme('DARK') === 'dark');
  check("normalizeTheme('  dark ') 去空白", box.normalizeTheme('  dark ') === 'dark');
  check("normalizeTheme(undefined) → null", box.normalizeTheme(undefined) === null);
  check("normalizeTheme(null) → null", box.normalizeTheme(null) === null);
  check("normalizeTheme('') → null", box.normalizeTheme('') === null);

  // ---- 契约 2：inIframe 判定 ----
  globalThis.window = { parent: { postMessage() {} } };
  check('iframe 内 inIframe() === true', box.inIframe() === true);

  const selfWin = {};
  selfWin.parent = selfWin; // window.parent === window ⇒ 独立页面
  globalThis.window = selfWin;
  check('独立页面 inIframe() === false', box.inIframe() === false);

  // ---- 契约 3：withTimeout 必须能从不 resolve 的 Promise 脱身 ----
  const hang = new Promise(() => {}); // 永不 settle，模拟无响应宿主
  const t0 = Date.now();
  const got = await box.withTimeout(hang, 200, 'FALLBACK');
  const cost = Date.now() - t0;
  check('挂起 Promise 走 fallback', got === 'FALLBACK', `返回值=${JSON.stringify(got)}`);
  check('挂起 Promise 按时脱身', cost >= 180 && cost < 1200, `耗时=${cost}ms`);

  // 正常 resolve 时不应被超时覆盖
  const fast = await box.withTimeout(Promise.resolve('REAL'), 200, 'FALLBACK');
  check('正常 resolve 保留真实值', fast === 'REAL', `返回值=${JSON.stringify(fast)}`);

  // reject 也必须被吞掉并走 fallback（SDK 在无宿主时就是 reject）
  const rejected = await box.withTimeout(
    Promise.reject(new Error('Host bridge is not available outside iframe or app runtime')),
    200,
    'FALLBACK',
  );
  check('reject 被吞掉并走 fallback', rejected === 'FALLBACK', `返回值=${JSON.stringify(rejected)}`);

  process.stdout.write(JSON.stringify(results));
})();
"""


def main() -> int:
    src = ROOT / "frontend" / "src" / "fnos.js"
    if not src.exists():
        print(f"缺少前端源文件：{src}")
        return 1

    driver = ROOT / ".sdk_integration_driver.cjs"
    driver.write_text(DRIVER, encoding="utf-8")
    try:
        proc = subprocess.run(
            [NODE, str(driver), str(src)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    finally:
        driver.unlink(missing_ok=True)

    if proc.returncode != 0:
        print("驱动执行失败：")
        print(proc.stderr)
        return 1

    results = json.loads(proc.stdout)
    failures = 0
    for r in results:
        mark = "通过" if r["ok"] else "★失败"
        detail = f"  （{r['detail']}）" if r.get("detail") else ""
        print(f"[{mark}] {r['name']}{detail}")
        if not r["ok"]:
            failures += 1

    print("-" * 46)
    print(f"{len(results) - failures}/{len(results)} 条 SDK 集成契约成立")
    if failures:
        print(f"\n存在 {failures} 处失败，需检查 frontend/src/fnos.js 的 SDK 守卫逻辑")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
