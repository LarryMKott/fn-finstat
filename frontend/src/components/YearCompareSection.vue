<script setup>
/* 年度对比：本年 vs 去年的月度支出柱状图、年度汇总与分类对比表。
 * 同比增减沿用中国用户直觉：支出增加用红系（income 语义色在此表示"不利变化"），
 * 减少用绿系，与图表主色阶保持一致 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { api } from "../api";
import echarts from "../charts";
import { axisBase, chartBase, chartTokens } from "../chartTheme";
import { fmtMoney } from "../format";
import { isDark } from "../theme";
import { store } from "../store";
import { toast } from "../toast";
import AppIcon from "./AppIcon.vue";

function nowYear() {
  return new Date().getFullYear();
}

const year = ref(nowYear());
const data = ref(null);
const chartEl = ref(null);
let chart = null;

async function load() {
  try {
    data.value = await api(`/api/stat/year_comparison?year=${year.value}`);
    await nextTick();
    render();
  } catch (err) {
    toast("年度对比加载失败：" + err.message, true);
  }
}

function yoy(now, prev) {
  if (!prev) return null;
  return Math.round(((now - prev) / prev) * 100);
}

function render() {
  const el = chartEl.value;
  if (!el || !data.value) return;
  if (!chart) chart = echarts.init(el);
  const t = chartTokens();
  const axis = axisBase();
  const d = data.value;
  chart.setOption(
    {
      ...chartBase(),
      legend: {
        data: [`${d.year}`, `${d.last_year}`],
        textStyle: { color: t.subtext, fontSize: 12 },
        icon: "roundRect",
        itemWidth: 10,
        itemHeight: 10,
        top: 0,
        right: 0,
      },
      xAxis: {
        type: "category",
        data: d.monthly.map((m) => m.month + "月"),
        ...axis,
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        ...axis,
        axisLine: { show: false },
        axisLabel: { formatter: (v) => "¥" + v, color: t.subtext, fontSize: 11 },
      },
      series: [
        {
          name: String(d.year),
          type: "bar",
          barMaxWidth: 18,
          itemStyle: { color: t.expense, borderRadius: [4, 4, 0, 0] },
          data: d.monthly.map((m) => m.this_expense),
        },
        {
          name: String(d.last_year),
          type: "bar",
          barMaxWidth: 18,
          itemStyle: { color: t.neutral, borderRadius: [4, 4, 0, 0], opacity: 0.55 },
          data: d.monthly.map((m) => m.last_expense),
        },
      ],
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
watch(isDark, () => render());

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
      <h3>年度对比</h3>
      <div class="heatmap-toolbar">
        <button class="btn mini icon" aria-label="上一年" @click="shiftYear(-1)">
          <AppIcon name="chevronDown" :size="14" style="transform: rotate(90deg)" />
        </button>
        <span class="heatmap-year">{{ year }}</span>
        <button class="btn mini icon" aria-label="下一年" @click="shiftYear(1)">
          <AppIcon name="chevronDown" :size="14" style="transform: rotate(-90deg)" />
        </button>
      </div>
    </div>

    <div v-if="data" class="year-summary">
      <span>今年支出 <b>{{ fmtMoney(data.this_expense) }}</b></span>
      <span v-if="yoy(data.this_expense, data.last_expense) !== null">
        较去年
        <b :class="data.this_expense >= data.last_expense ? 'up' : 'down'">
          {{ data.this_expense >= data.last_expense ? "+" : "" }}{{ yoy(data.this_expense, data.last_expense) }}%
        </b>
      </span>
      <span>去年支出 <b>{{ fmtMoney(data.last_expense) }}</b></span>
      <span>今年收入 <b>{{ fmtMoney(data.this_income) }}</b></span>
    </div>

    <div ref="chartEl" class="chart"></div>

    <div v-if="data && data.categories.length" class="table-wrap year-cat-table">
      <div class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>分类</th>
              <th class="num">{{ data.year }} 支出</th>
              <th class="num">{{ data.last_year }} 支出</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in data.categories" :key="c.category">
              <td class="cell-primary">{{ c.category }}</td>
              <td class="num">{{ fmtMoney(c.this_year) }}</td>
              <td class="num cell-secondary">{{ fmtMoney(c.last_year) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <p v-if="data && !data.categories.length" class="empty">两年均无支出数据</p>
  </div>
</template>

<style scoped>
.section-head__hint {
  flex: 1 1 100%;
  order: 3;
}
</style>
