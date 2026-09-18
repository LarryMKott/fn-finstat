/* 日期与时间范围工具：各面板"本月/本年"预设、默认月份、输入框格式共用 */

export const pad2 = (n) => String(n).padStart(2, "0");

/** 当月窗口（含首尾）：{ start: "YYYY-MM-01", end: "YYYY-MM-DD" } */
function currentMonthWindow(now = new Date()) {
  const y = now.getFullYear();
  const m = now.getMonth();
  const last = new Date(y, m + 1, 0).getDate();
  return { start: `${y}-${pad2(m + 1)}-01`, end: `${y}-${pad2(m + 1)}-${pad2(last)}` };
}

/** 本年窗口（含首尾）：{ start: "YYYY-01-01", end: "YYYY-12-31" } */
function currentYearWindow(now = new Date()) {
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

/* ---- 周期边界（与后端 utils/period.py 的 period_range / prev_period 对齐）----
 * AI 报告附录「点数字看来源」需要把周期标识换算成流水页筛选口径，
 * 前后端口径不一致会导致追溯跳过去看到的流水不是报告统计的那批。 */

/** 周期标识 → 起止日期（含端点）：month "2026-09" / quarter "2026-Q3" /
 *  half "2026-H2" / year "2026"；格式非法返回空窗口 */
export function periodRange(type, value) {
  const str = String(value || "");
  if (type === "month" && /^\d{4}-\d{2}$/.test(str)) {
    const [y, m] = str.split("-").map(Number);
    const last = new Date(y, m, 0).getDate();
    return { start: `${y}-${pad2(m)}-01`, end: `${y}-${pad2(m)}-${pad2(last)}` };
  }
  if (type === "quarter" && /^\d{4}-Q[1-4]$/.test(str)) {
    const y = Number(str.slice(0, 4));
    const q = Number(str.slice(6));
    const first = (q - 1) * 3 + 1;
    const last = q * 3;
    const lastDay = new Date(y, last, 0).getDate();
    return {
      start: `${y}-${pad2(first)}-01`,
      end: `${y}-${pad2(last)}-${pad2(lastDay)}`,
    };
  }
  if (type === "half" && /^\d{4}-H[12]$/.test(str)) {
    const y = Number(str.slice(0, 4));
    const h = Number(str.slice(6));
    return h === 1
      ? { start: `${y}-01-01`, end: `${y}-06-30` }
      : { start: `${y}-07-01`, end: `${y}-12-31` };
  }
  if (type === "year" && /^\d{4}$/.test(str)) {
    const y = Number(str);
    return { start: `${y}-01-01`, end: `${y}-12-31` };
  }
  return { start: "", end: "" };
}

/** 上一周期同维标识（月→上月、季→同年前一季、半年→同年前一半年、年→前一年） */
export function prevPeriod(type, value) {
  const str = String(value || "");
  if (type === "month" && /^\d{4}-\d{2}$/.test(str)) {
    const [y, m] = str.split("-").map(Number);
    return m === 1 ? `${y - 1}-12` : `${y}-${pad2(m - 1)}`;
  }
  if (type === "quarter" && /^\d{4}-Q[1-4]$/.test(str)) {
    const y = Number(str.slice(0, 4));
    const q = Number(str.slice(6));
    return q === 1 ? `${y - 1}-Q4` : `${y}-Q${q - 1}`;
  }
  if (type === "half" && /^\d{4}-H[12]$/.test(str)) {
    const y = Number(str.slice(0, 4));
    const h = Number(str.slice(6));
    return h === 1 ? `${y - 1}-H2` : `${y}-H1`;
  }
  if (type === "year" && /^\d{4}$/.test(str)) {
    return String(Number(str) - 1);
  }
  return "";
}

/** 今日日期 "YYYY-MM-DD"（资产快照默认值） */
export function todayStr(now = new Date()) {
  return `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`;
}

/** 本地时间（datetime-local 输入框格式），替代 toISOString 的 UTC 偏移问题 */
export function nowLocalMinute(now = new Date()) {
  return `${todayStr(now)}T${pad2(now.getHours())}:${pad2(now.getMinutes())}`;
}

/** ISO 时间串（如 2026-09-17T14:54:26+08:00）→ "YYYY-MM-DD"；形状不符时返回空串
 *
 * 只做切片不做时区换算：后端返回的时间串自带发布方时区偏移，日期部分即发布当天，
 * 交给 Date 解析反而会按运行环境时区偏成前一天。 */
export function isoDate(value) {
  const text = String(value || "");
  return /^\d{4}-\d{2}-\d{2}/.test(text) ? text.slice(0, 10) : "";
}
