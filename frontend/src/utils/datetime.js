/* 日期与时间范围工具：各面板"本月/本年"预设、默认月份、输入框格式共用 */

export const pad2 = (n) => String(n).padStart(2, "0");

/** 当月窗口（含首尾）：{ start: "YYYY-MM-01", end: "YYYY-MM-DD" } */
export function currentMonthWindow(now = new Date()) {
  const y = now.getFullYear();
  const m = now.getMonth();
  const last = new Date(y, m + 1, 0).getDate();
  return { start: `${y}-${pad2(m + 1)}-01`, end: `${y}-${pad2(m + 1)}-${pad2(last)}` };
}

/** 本年窗口（含首尾）：{ start: "YYYY-01-01", end: "YYYY-12-31" } */
export function currentYearWindow(now = new Date()) {
  const y = now.getFullYear();
  return { start: `${y}-01-01`, end: `${y}-12-31` };
}

/** 预设范围（month/year/其他=全部）：与看板、消费地图共用的同一段"本月/本年"规则 */
export function presetWindow(type, now = new Date()) {
  if (type === "month") return currentMonthWindow(now);
  if (type === "year") return currentYearWindow(now);
  return { start: "", end: "" };
}

/** 本月 "YYYY-MM"（预算页、AI 报告的月份默认值） */
export function currentMonth(now = new Date()) {
  return `${now.getFullYear()}-${pad2(now.getMonth() + 1)}`;
}

/** 上月 "YYYY-MM"（AI 月报默认统计上月） */
export function previousMonth(now = new Date()) {
  const d = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  return currentMonth(d);
}

/** 今日日期 "YYYY-MM-DD"（资产快照默认值） */
export function todayStr(now = new Date()) {
  return `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`;
}

/** 本地时间（datetime-local 输入框格式），替代 toISOString 的 UTC 偏移问题 */
export function nowLocalMinute(now = new Date()) {
  return `${todayStr(now)}T${pad2(now.getHours())}:${pad2(now.getMinutes())}`;
}
