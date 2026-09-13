<script setup>
/* 日历热力图：按日支出热力图（ECharts calendar），可切换年份。
 * 配色改为与设计令牌一致的墨绿阶，替代原先的通用蓝阶 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api } from "../api";
import echarts from "../charts";
import { chartTokens } from "../chartTheme";
import { isDark } from "../theme";
import { store } from "../store";
import { toast } from "../toast";
import AppIcon from "./AppIcon.vue";

function nowYear() {
  return new Date().getFullYear();
}

const year = ref(nowYear());
const chartEl = ref(null);
let chart = null;
/* 请求序号：快速切换年份时只让最新一次响应生效 */
let loadSeq = 0;
/* 最近一次成功数据：主题切换仅需用当前数据重绘，不必重新请求 */
let lastData = null;

async function load() {
  const seq = ++loadSeq;
  try {
    const data = await api(`/api/stat/daily_heatmap?year=${year.value}`);
    if (seq !== loadSeq) return; // 过期响应直接丢弃
    lastData = data;
    await nextTick();
    render(data);
  } catch (err) {
    if (seq === loadSeq) toast("热力图加载失败：" + err.message, true);
  }
}

function render(data) {
  const el = chartEl.value;
  if (!el) return;
  if (!chart) chart = echarts.init(el);
  const t = chartTokens();
  const points = data.map((d) => [d.date, d.expense]);
  const maxVal = Math.max(1, ...data.map((d) => d.expense));
  chart.setOption(
    {
      textStyle: { color: t.text, fontFamily: "inherit", fontSize: 12 },
      tooltip: {
        backgroundColor: t.surface,
        borderColor: t.border,
        borderWidth: 1,
        textStyle: { color: t.text, fontSize: 12 },
        extraCssText: "border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.14);padding:8px 12px;",
        formatter: (p) => `${p.value[0]}<br/>支出：¥${Number(p.value[1]).toFixed(2)}`,
      },
      visualMap: {
        min: 0,
        max: maxVal,
        calculable: false,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        itemWidth: 12,
        itemHeight: 90,
        textStyle: { color: t.subtext, fontSize: 11 },
        inRange: {
          /* 深底到主色的连续过渡，浅色主题从近白起步避免突兀 */
          color: t.dark
            ? ["#161a20", "#1e3a30", "#2d5443", "#4e8467", "#7fbe9c"]
            : ["#f7f5f1", "#dbe8e1", "#a9cbb9", "#5f8f78", "#2d5443"],
        },
      },
      calendar: {
        top: 30,
        left: 38,
        right: 12,
        cellSize: ["auto", 15],
        range: String(year.value),
        itemStyle: { color: "transparent", borderColor: t.splitLine, borderWidth: 1 },
        splitLine: { show: false },
        yearLabel: { show: false },
        monthLabel: { color: t.subtext, fontSize: 11 },
        dayLabel: { color: t.subtext, fontSize: 11, nameMap: ["日", "一", "二", "三", "四", "五", "六"] },
      },
      series: [{ type: "heatmap", coordinateSystem: "calendar", data: points }],
    },
    true,
  );
}

function shiftYear(delta) {
  year.value = Number(year.value) + delta;
}

watch(year, load);
watch(
  () => store.tab === "dashboard",
  (active) => {
    if (active) load();
  },
  { immediate: true },
);
/* 主题切换只需换配色重绘；看板未激活时容器是 display:none，
 * 此时 init/重绘会得到 0 尺寸实例，回到看板后表现为空白 */
watch(isDark, () => {
  if (store.tab === "dashboard" && lastData) render(lastData);
});

function onResize() {
  if (chart) chart.resize();
}

onMounted(() => window.addEventListener("resize", onResize));
onBeforeUnmount(() => {
  window.removeEventListener("resize", onResize);
  if (chart) chart.dispose();
});
</script>

<template>
  <div class="chart-box">
    <div class="section-head">
      <h3>消费日历</h3>
      <div class="heatmap-toolbar">
        <button class="btn mini icon" aria-label="上一年" @click="shiftYear(-1)">
          <AppIcon name="chevronDown" :size="14" style="transform: rotate(90deg)" />
        </button>
        <span class="heatmap-year">{{ year }}</span>
        <button class="btn mini icon" aria-label="下一年" @click="shiftYear(1)">
          <AppIcon name="chevronDown" :size="14" style="transform: rotate(-90deg)" />
        </button>
      </div>
      <span class="section-head__hint">颜色越深表示当日支出越高，一眼定位大额消费日</span>
    </div>
    <div ref="chartEl" class="chart heatmap-chart"></div>
  </div>
</template>

<style scoped>
/* 标题行内嵌年份切换器：标题、切换、说明同一行，压缩纵向空间 */
.section-head {
  align-items: center;
}
.section-head__hint {
  flex: 1 1 100%;
  order: 3;
}
.heatmap-toolbar {
  margin-bottom: 0;
}
@media (max-width: 640px) {
  .section-head {
    gap: var(--space-2);
  }
}
</style>
