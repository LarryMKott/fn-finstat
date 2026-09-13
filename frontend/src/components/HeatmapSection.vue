<script setup>
/* 日历热力图：按日支出热力图（ECharts calendar），可切换年份。
 * 配色与设计令牌一致的墨绿阶（heatRamp），替代原先的通用蓝阶 */
import { nextTick, ref, watch } from "vue";
import { dailyHeatmap } from "../api/stat";
import { chartBase, chartTokens, heatRamp } from "../utils/chartTheme";
import { fmtMoney } from "../utils/format";
import { useChart } from "../composables/useChart";
import { runTask } from "../composables/useLoading";
import { store } from "../store";
import AppIcon from "./AppIcon.vue";

const year = ref(new Date().getFullYear());
const chartEl = ref(null);
/* 最近一次成功数据：主题切换仅需用当前数据重绘，不必重新请求 */
let lastData = null;

const { render } = useChart(chartEl, (chart) => renderHeatmap(chart), {
  /* 看板未激活时容器是 display:none，主题重绘跳过；切回看板会重新 load */
  canRender: () => store.tab === "dashboard",
});

/* 切换年份是查询：用 latest 模式（新请求接管浮层显示），但 latest 不取消已在途的
 * Promise，旧响应返回后仍会覆盖新数据，故保留一层序号校验兜底。 */
let loadSeq = 0;

async function load() {
  await runTask({
    key: "heatmap:load",
    title: "加载消费日历",
    detail: `正在汇总 ${year.value} 年每日支出…`,
    mode: "latest",
    rethrow: false,
    successText: "日历已更新",
    task: async () => {
      const seq = ++loadSeq;
      let data;
      try {
        data = await dailyHeatmap({ year: year.value });
      } catch (err) {
        throw new Error("热力图加载失败：" + err.message);
      }
      /* 已有更新的请求发出：本次响应作废 */
      if (seq !== loadSeq) return null;
      lastData = data;
      await nextTick();
      render();
      return data;
    },
  });
}

function renderHeatmap(chart) {
  const data = lastData || [];
  const t = chartTokens();
  const base = chartBase();
  const points = data.map((d) => [d.date, d.expense]);
  const maxVal = Math.max(1, ...data.map((d) => d.expense));
  chart.setOption(
    {
      ...base,
      tooltip: {
        ...base.tooltip,
        formatter: (p) => `${p.value[0]}<br/>支出：${fmtMoney(p.value[1])}`,
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
          /* 色阶统一走 chartTheme.heatRamp()：从 CSS 基础调色板逐阶取值 */
          color: heatRamp(),
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
