/* ECharts 主题适配：画布读不到 CSS 变量，需从 :root 计算后的样式表取值。
 * 集中在此处管理，避免每个图表组件各自硬编码颜色（原实现的重复来源）。 */
import { isDark } from "../theme";

function cssVar(name, fallback) {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name);
  return v ? v.trim() : fallback;
}

/* 与设计令牌一致的语义色：收入（红系）/ 支出（绿系）遵循本地化惯例 */
export function chartTokens() {
  const dark = isDark.value;
  return {
    dark,
    text: cssVar("--chart-text", dark ? "#e8e6e1" : "#1a1f1d"),
    subtext: cssVar("--chart-subtext", dark ? "#a8b0b5" : "#5c6560"),
    splitLine: cssVar("--chart-splitline", dark ? "#2b323b" : "#eae5dc"),
    surface: cssVar("--color-surface", dark ? "#1b2027" : "#ffffff"),
    border: cssVar("--color-border", dark ? "#2b323b" : "#eae5dc"),
    income: cssVar("--color-income", dark ? "#e08576" : "#c05548"),
    expense: cssVar("--color-expense", dark ? "#71b891" : "#47825f"),
    primary: cssVar("--color-primary", dark ? "#6fa98b" : "#2d5443"),
    accent: cssVar("--color-accent", dark ? "#d4a843" : "#c19434"),
    neutral: cssVar("--color-text-tertiary", dark ? "#7c858c" : "#7d8782"),
    warn: cssVar("--color-warn", dark ? "#e0ac5c" : "#d99b3c"),
  };
}

/* 分类调色板：暖调为主，与整体视觉语言一致，避免出现刺眼的高饱和色 */
function categoryPalette() {
  const dark = isDark.value;
  return dark
    ? ["#6fa98b", "#d4a843", "#e08576", "#7fa8cc", "#b090c8", "#6fb8b0", "#d99b3c", "#9fb37a"]
    : ["#2d5443", "#c19434", "#c05548", "#3f6c96", "#7a5f9c", "#3d7f78", "#b8802a", "#6b8347"];
}

/* 热力型图表的色阶（消费日历、消费地图等密度可视化共用）
 *
 * 取墨绿（pine）色系由浅到深，避免引入与整体视觉无关的第二色相。
 * 深浅方向随主题反转，且两端始终符合「低值隐、高值显」：
 *   浅色主题 → 低值近纸色、高值浓墨绿（由淡到浓）
 *   深色主题 → 低值近底色、高值亮绿（暗底上才看得见）
 * 从 CSS 令牌逐阶取值，保证与 styles/tokens.css 的基础调色板同源，不在此硬编码。
 */
export function heatRamp() {
  const dark = isDark.value;
  const steps = dark
    ? ["--pine-900", "--pine-800", "--pine-600", "--pine-500", "--pine-300"]
    : ["--pine-50", "--pine-100", "--pine-200", "--pine-400", "--pine-600"];
  // 回退值与基础调色板逐一对应（CSS 变量读取失败时仍能得到同源色阶）
  const fallback = dark
    ? ["#12241e", "#1a322a", "#2d5443", "#3d6b55", "#8db3a1"]
    : ["#f0f5f2", "#dbe8e1", "#b8d1c5", "#5f8f78", "#2d5443"];
  return steps.map((name, i) => cssVar(name, fallback[i]));
}

/* 图表通用基座：统一文字色、提示框、网格留白与配色 */
export function chartBase() {
  const t = chartTokens();
  return {
    color: categoryPalette(),
    textStyle: { color: t.text, fontFamily: "inherit", fontSize: 12 },
    tooltip: {
      backgroundColor: cssVar("--chart-tooltip-bg", t.surface),
      borderColor: cssVar("--chart-tooltip-border", t.border),
      borderWidth: 1,
      textStyle: { color: t.text, fontSize: 12 },
      extraCssText: "border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.14);padding:8px 12px;",
    },
    grid: { left: 8, right: 12, top: 34, bottom: 4, containLabel: true },
  };
}

/* 坐标轴通用样式 */
export function axisBase() {
  const t = chartTokens();
  return {
    axisLine: { lineStyle: { color: t.splitLine } },
    axisTick: { show: false },
    axisLabel: { color: t.subtext, fontSize: 11 },
    splitLine: { lineStyle: { color: t.splitLine, type: "dashed" } },
  };
}
