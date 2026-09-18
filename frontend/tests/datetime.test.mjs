/* 周期边界工具（utils/datetime.js 的 periodRange / prevPeriod）自测。
 *
 * 这些函数服务于 AI 报告附录「点数字看来源」（T-6.5）：把周期标识换算成
 * 流水页筛选口径。与后端 utils/period.py 的 period_range / prev_period
 * 必须逐位对齐——跳转后看到的流水若与报告统计的不是同一批，追溯就成了
 * 误导。用例值逐条取自后端测试（tests/test_api.py::service_month_range 等）
 * 与日历事实（闰年、30/31 天月份）。
 *
 * 运行： npm test   （node --test tests/datetime.test.mjs 被一并匹配） */
import assert from "node:assert/strict";

import { periodRange, prevPeriod } from "../src/utils/datetime.js";

let passed = 0;
const failures = [];

async function test(name, fn) {
  try {
    await fn();
    passed += 1;
    console.log(`  ✓ ${name}`);
  } catch (err) {
    failures.push({ name, err });
    console.error(`  ✗ ${name}\n    ${err.message}`);
  }
}

await test("月度边界含闰年", () => {
  assert.deepEqual(periodRange("month", "2026-09"), {
    start: "2026-09-01",
    end: "2026-09-30",
  });
  assert.deepEqual(periodRange("month", "2024-02"), {
    start: "2024-02-01",
    end: "2024-02-29",
  });
});

await test("季度 / 半年 / 年度边界", () => {
  assert.deepEqual(periodRange("quarter", "2026-Q1"), {
    start: "2026-01-01",
    end: "2026-03-31",
  });
  assert.deepEqual(periodRange("quarter", "2026-Q4"), {
    start: "2026-10-01",
    end: "2026-12-31",
  });
  assert.deepEqual(periodRange("half", "2026-H1"), {
    start: "2026-01-01",
    end: "2026-06-30",
  });
  assert.deepEqual(periodRange("half", "2026-H2"), {
    start: "2026-07-01",
    end: "2026-12-31",
  });
  assert.deepEqual(periodRange("year", "2026"), {
    start: "2026-01-01",
    end: "2026-12-31",
  });
});

await test("上一周期跨年回退", () => {
  assert.equal(prevPeriod("month", "2026-01"), "2025-12");
  assert.equal(prevPeriod("month", "2026-09"), "2026-08");
  assert.equal(prevPeriod("quarter", "2026-Q1"), "2025-Q4");
  assert.equal(prevPeriod("quarter", "2026-Q3"), "2026-Q2");
  assert.equal(prevPeriod("half", "2026-H1"), "2025-H2");
  assert.equal(prevPeriod("year", "2026"), "2025");
});

await test("非法周期标识返回空窗口 / 空串，不抛异常", () => {
  assert.deepEqual(periodRange("month", "bad"), { start: "", end: "" });
  assert.deepEqual(periodRange("evil", "2026-09"), { start: "", end: "" });
  assert.equal(prevPeriod("evil", "2026-09"), "");
});

console.log("");
if (failures.length) {
  console.log(`${passed} passed, ${failures.length} failed`);
  process.exit(1);
}
console.log(`${passed} passed`);
