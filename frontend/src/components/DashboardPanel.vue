<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { api } from "../api";
import echarts from "../charts";
import { fmtMoney } from "../format";
import { store } from "../store";
import { toast } from "../toast";

const MEDAL_COLORS = ["#2563eb", "#16a34a", "#f59e0b"];

const summary = ref({ income: 0, expense: 0, net: 0 });
const trend = ref([]);
const pie = ref([]);
const top = ref([]);
const trendEl = ref(null);
const pieEl = ref(null);
const range = reactive({ start: "", end: "" });

let trendChart = null;
let pieChart = null;

const pieItems = computed(() => pie.value.filter((d) => d.value > 0));

function rangeParams() {
  const p = new URLSearchParams();
  if (range.start) p.set("start", range.start);
  if (range.end) p.set("end", range.end);
  const qs = p.toString();
  return qs ? `?${qs}` : "";
}

function chartBase() {
  return {
    color: ["#2563eb", "#16a34a", "#dc2626", "#f59e0b", "#8b5cf6", "#06b6d4", "#ec4899", "#84cc16"],
    tooltip: { trigger: "axis" },
    grid: { left: 16, right: 16, top: 30, bottom: 8, containLabel: true },
  };
}

function rankStyle(i) {
  return i < MEDAL_COLORS.length ? { background: MEDAL_COLORS[i], color: "#fff" } : null;
}

async function load() {
  try {
    const qs = rangeParams();
    const [s, t, p, top10] = await Promise.all([
      api("/api/stat/summary" + qs),
      api("/api/stat/month_trend" + qs),
      api("/api/stat/category_pie" + qs),
      api("/api/stat/merchant_top?limit=10" + (qs ? "&" + qs.slice(1) : "")),
    ]);
    summary.value = s;
    trend.value = t;
    pie.value = p;
    top.value = top10;
    /* 等面板可见后再初始化图表，避免对 display:none 容器初始化得到 0 尺寸 */
    await nextTick();
    renderTrend();
    renderPie();
  } catch (err) {
    toast("看板加载失败：" + err.message, true);
  }
}

function setPreset(type) {
  const now = new Date();
  const y = now.getFullYear();
  const m = now.getMonth();
  if (type === "month") {
    range.start = `${y}-${String(m + 1).padStart(2, "0")}-01`;
    range.end = `${y}-${String(m + 1).padStart(2, "0")}-${new Date(y, m + 1, 0).getDate()}`;
  } else if (type === "year") {
    range.start = `${y}-01-01`;
    range.end = `${y}-12-31`;
  } else {
    range.start = "";
    range.end = "";
  }
  load();
}

function renderTrend() {
  const el = trendEl.value;
  if (!el) return;
  if (!trendChart) trendChart = echarts.init(el);
  trendChart.setOption(
    {
      ...chartBase(),
      legend: { data: ["收入", "支出"] },
      xAxis: { type: "category", data: trend.value.map((d) => d.month) },
      yAxis: { type: "value", axisLabel: { formatter: (v) => "¥" + v } },
      series: [
        { name: "收入", type: "line", smooth: true, data: trend.value.map((d) => d.income), areaStyle: { opacity: 0.08 } },
        { name: "支出", type: "line", smooth: true, data: trend.value.map((d) => d.expense), areaStyle: { opacity: 0.08 } },
      ],
    },
    true,
  );
}

function renderPie() {
  const el = pieEl.value;
  if (!el) return;
  if (!pieChart) pieChart = echarts.init(el);
  pieChart.setOption(
    {
      ...chartBase(),
      tooltip: { trigger: "item", formatter: "{b}: ¥{c} ({d}%)" },
      series: [
        {
          type: "pie",
          radius: ["40%", "68%"],
          center: ["50%", "52%"],
          data: pieItems.value,
          label: { formatter: "{b}\n{d}%" },
        },
      ],
    },
    true,
  );
}

function onResize() {
  if (trendChart) trendChart.resize();
  if (pieChart) pieChart.resize();
}

watch(
  () => store.tab === "dashboard",
  (active) => {
    if (active) load();
  },
  { immediate: true },
);

onMounted(() => window.addEventListener("resize", onResize));
onBeforeUnmount(() => {
  window.removeEventListener("resize", onResize);
  if (trendChart) trendChart.dispose();
  if (pieChart) pieChart.dispose();
});
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'dashboard' }">
    <div class="filter-bar">
      <input v-model="range.start" type="date" title="起始日期" />
      <span class="sep">至</span>
      <input v-model="range.end" type="date" title="结束日期" />
      <button class="btn primary" @click="load">查询</button>
      <button class="btn" @click="setPreset('month')">本月</button>
      <button class="btn" @click="setPreset('year')">本年</button>
      <button class="btn" @click="setPreset('all')">全部</button>
    </div>

    <div class="cards">
      <div class="card card-income">
        <div class="card-label">总收入</div>
        <div class="card-value">{{ fmtMoney(summary.income) }}</div>
      </div>
      <div class="card card-expense">
        <div class="card-label">总支出</div>
        <div class="card-value">{{ fmtMoney(summary.expense) }}</div>
      </div>
      <div class="card card-net">
        <div class="card-label">净结余</div>
        <div class="card-value">{{ fmtMoney(summary.net) }}</div>
      </div>
    </div>

    <div class="chart-grid">
      <div class="chart-box">
        <h3>月度收支趋势</h3>
        <div ref="trendEl" class="chart"></div>
      </div>
      <div class="chart-box">
        <h3>分类支出占比</h3>
        <div ref="pieEl" class="chart"></div>
      </div>
    </div>

    <div class="chart-box">
      <h3>商户消费 TOP</h3>
      <ol class="merchant-top">
        <li v-if="!top.length" class="empty">暂无支出数据</li>
        <li v-for="(d, i) in top" :key="d.merchant + '-' + i">
          <span><span class="rank" :style="rankStyle(i)">{{ i + 1 }}</span>{{ d.merchant }}</span>
          <span class="amount">{{ fmtMoney(d.amount) }}<small style="color: #94a3b8">（{{ d.count }}笔）</small></span>
        </li>
      </ol>
    </div>
  </section>
</template>
