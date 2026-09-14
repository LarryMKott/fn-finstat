<script setup>
/* 统计看板：原设计把汇总卡、预算、两张图表、热力图、年度对比、商户排行全部平铺，
 * 页面很长且没有视觉重点。现按信息层级重组：
 *   1. 概览层 —— 净结余作为视觉焦点（主卡），收入/支出为次级卡
 *   2. 对比层 —— 月度趋势与分类结构并排，一眼看结构
 *   3. 明细层 —— 预算进度、消费日历、年度对比、商户排行依次展开 */
import { computed, nextTick, reactive, ref, watch } from "vue";
import { categoryPie, merchantTop, monthTrend, statSummary } from "../api/stat";
import { axisBase, chartBase, chartTokens } from "../utils/chartTheme";
import { fmtMoney } from "../utils/format";
import { presetWindow } from "../utils/datetime";
import { useChart } from "../composables/useChart";
import { runTask } from "../composables/useLoading";
import { store } from "../store";
import AppIcon from "./AppIcon.vue";
import BudgetSection from "./BudgetSection.vue";
import HeatmapSection from "./HeatmapSection.vue";
import YearCompareSection from "./YearCompareSection.vue";
import AIReportModal from "./AIReportModal.vue";

const summary = ref({ income: 0, expense: 0, net: 0 });
const trend = ref([]);
const pie = ref([]);
const top = ref([]);
const trendEl = ref(null);
const pieEl = ref(null);
const range = reactive({ start: "", end: "" });
const reportShow = ref(false);

const trendChart = useChart(trendEl, (chart) => renderTrend(chart));
const pieChart = useChart(pieEl, (chart) => renderPie(chart));

const pieItems = computed(() => pie.value.filter((d) => d.value > 0));

/* 结余率：净结余占收入的比例，帮助判断当月收支健康度 */
const savingRate = computed(() => {
  const inc = Number(summary.value.income || 0);
  if (inc <= 0) return null;
  return Math.round((Number(summary.value.net || 0) / inc) * 100);
});

/* 当前筛选范围的人类可读描述 */
const rangeLabel = computed(() => {
  if (!range.start && !range.end) return "全部时间";
  if (range.start && range.end) return `${range.start} ~ ${range.end}`;
  return range.start ? `${range.start} 起` : `截至 ${range.end}`;
});

function rangeParams() {
  const p = new URLSearchParams();
  if (range.start) p.set("start", range.start);
  if (range.end) p.set("end", range.end);
  const qs = p.toString();
  return qs ? `?${qs}` : "";
}

function rankStyle(i) {
  /* 前三名用品牌绿阶区分，其余保持中性，避免过多色彩干扰 */
  if (i === 0) return { background: "var(--color-primary)", color: "var(--color-on-primary)" };
  if (i < 3) return { background: "var(--color-primary-soft)", color: "var(--color-primary)" };
  return null;
}

async function load() {
  await runTask({
    key: "dashboard:load",
    title: "加载统计看板",
    detail: "正在汇总收支与趋势…",
    /* 看板是查询，允许新请求接管，快速切换时间范围时旧响应作废 */
    mode: "latest",
    progress: 10,
    rethrow: false,
    successText: "看板已更新",
    task: async (update, isCurrent) => {
      const qs = rangeParams();
      const params = Object.fromEntries(new URLSearchParams(qs.replace(/^\?/, "")));
      /* 四个请求并发，但逐个汇报进度，让用户知道还剩多少 */
      let finished = 0;
      const track = (p) =>
        p.then((r) => {
          finished += 1;
          update({
            detail: `正在加载图表数据 ${finished}/4…`,
            progress: 10 + finished * 20,
          });
          return r;
        });
      try {
        const [s, t, p, top10] = await Promise.all([
          track(statSummary(params)),
          track(monthTrend(params)),
          track(categoryPie(params)),
          track(merchantTop({ limit: 10, ...params })),
        ]);
        /* latest 只作废浮层状态、不取消在途 Promise：快速切换时间范围时
         * 慢的旧响应会后到，已被新请求接管即作废，不得覆盖新数据 */
        if (!isCurrent()) return null;
        summary.value = s;
        trend.value = t;
        pie.value = p;
        top.value = top10;
        update({ detail: "正在绘制图表…", progress: 95 });
        /* 等面板可见后再初始化图表，避免对 display:none 容器初始化得到 0 尺寸 */
        await nextTick();
        trendChart.render();
        pieChart.render();
      } catch (err) {
        throw new Error("看板加载失败：" + err.message);
      }
    },
  });
}

function setPreset(type) {
  const { start, end } = presetWindow(type);
  range.start = start;
  range.end = end;
  load();
}

function renderTrend(chart) {
  const t = chartTokens();
  const axis = axisBase();
  const base = chartBase();
  chart.setOption(
    {
      ...base,
      legend: {
        data: ["收入", "支出"],
        textStyle: { color: t.subtext, fontSize: 12 },
        icon: "roundRect",
        itemWidth: 10,
        itemHeight: 10,
        top: 0,
        right: 0,
      },
      xAxis: {
        type: "category",
        data: trend.value.map((d) => d.month),
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
          name: "收入",
          type: "line",
          smooth: true,
          symbol: "circle",
          symbolSize: 6,
          showSymbol: false,
          lineStyle: { width: 2.4, color: t.income },
          itemStyle: { color: t.income },
          areaStyle: {
            opacity: 0.1,
            color: {
              type: "linear",
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: t.income },
                { offset: 1, color: "transparent" },
              ],
            },
          },
          data: trend.value.map((d) => d.income),
        },
        {
          name: "支出",
          type: "line",
          smooth: true,
          symbol: "circle",
          symbolSize: 6,
          showSymbol: false,
          lineStyle: { width: 2.4, color: t.expense },
          itemStyle: { color: t.expense },
          areaStyle: {
            opacity: 0.1,
            color: {
              type: "linear",
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: t.expense },
                { offset: 1, color: "transparent" },
              ],
            },
          },
          data: trend.value.map((d) => d.expense),
        },
      ],
    },
    true,
  );
}

function renderPie(chart) {
  const t = chartTokens();
  const base = chartBase();
  chart.setOption(
    {
      ...base,
      tooltip: {
        ...base.tooltip,
        trigger: "item",
        formatter: (p) => `${p.name}<br/>${fmtMoney(p.value)} · ${p.percent}%`,
      },
      legend: {
        type: "scroll",
        orient: "vertical",
        right: 0,
        top: "center",
        itemWidth: 9,
        itemHeight: 9,
        itemGap: 9,
        icon: "circle",
        textStyle: { color: t.subtext, fontSize: 12 },
        formatter: (name) => (name.length > 7 ? name.slice(0, 7) + "…" : name),
      },
      series: [
        {
          type: "pie",
          radius: ["52%", "74%"],
          center: ["33%", "50%"],
          avoidLabelOverlap: true,
          itemStyle: { borderColor: t.surface, borderWidth: 2, borderRadius: 4 },
          label: { show: false },
          labelLine: { show: false },
          emphasis: {
            scale: true,
            scaleSize: 6,
            label: { show: false },
          },
          data: pieItems.value,
        },
      ],
    },
    true,
  );
}

watch(
  () => store.tab === "dashboard",
  (active) => {
    if (active) load();
  },
  { immediate: true },
);
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'dashboard' }">
    <!-- 时间范围：预设优先，减少手选日期的操作成本 -->
    <div class="filter-bar">
      <div class="range-presets">
        <button class="btn mini" @click="setPreset('month')">本月</button>
        <button class="btn mini" @click="setPreset('year')">本年</button>
        <button class="btn mini" @click="setPreset('all')">全部</button>
      </div>
      <span class="sep">|</span>
      <input v-model="range.start" type="date" title="起始日期" aria-label="起始日期" />
      <span class="sep">至</span>
      <input v-model="range.end" type="date" title="结束日期" aria-label="结束日期" />
      <button class="btn primary" @click="load">查询</button>
      <span class="range-label hint">当前：{{ rangeLabel }}</span>
      <button class="btn right" title="DeepSeek 生成周期消费分析报告（需在设置页配置 API Key）" @click="reportShow = true">
        <AppIcon name="sparkles" :size="15" />
        AI 报告
      </button>
    </div>

    <!-- 概览层：净结余为视觉焦点主卡 -->
    <div class="cards">
      <div class="card card--primary">
        <div class="card-label">净结余</div>
        <div class="card-value">{{ fmtMoney(summary.net) }}</div>
        <div class="card-meta">
          <template v-if="savingRate !== null">结余率 {{ savingRate }}% · 收入 {{ fmtMoney(summary.income) }}</template>
          <template v-else>收入 - 支出 = 净结余</template>
        </div>
      </div>
      <div class="card card-income">
        <div class="card-label">总收入</div>
        <div class="card-value amount--income">{{ fmtMoney(summary.income) }}</div>
        <div class="card-meta">{{ rangeLabel }}</div>
      </div>
      <div class="card card-expense">
        <div class="card-label">总支出</div>
        <div class="card-value amount--expense">{{ fmtMoney(summary.expense) }}</div>
        <div class="card-meta">{{ rangeLabel }}</div>
      </div>
    </div>

    <!-- 对比层：趋势 + 结构并排 -->
    <div class="chart-grid">
      <div class="chart-box">
        <div class="section-head">
          <h3>月度收支趋势</h3>
          <span class="section-head__hint">按月对比收入与支出变化</span>
        </div>
        <div ref="trendEl" class="chart"></div>
      </div>
      <div class="chart-box">
        <div class="section-head">
          <h3>分类支出占比</h3>
          <span class="section-head__hint">看清钱花在哪些方向</span>
        </div>
        <div ref="pieEl" class="chart"></div>
      </div>
    </div>

    <!-- 明细层：预算 → 日历 → 年度 → 商户 -->
    <BudgetSection />
    <HeatmapSection />
    <YearCompareSection />

    <div class="chart-box">
      <div class="section-head">
        <h3>商户消费 TOP</h3>
        <span class="section-head__hint">按累计支出金额排序</span>
      </div>
      <ol class="merchant-top">
        <li v-if="!top.length" class="empty">暂无支出数据</li>
        <li v-for="(d, i) in top" :key="d.merchant + '-' + i">
          <span><span class="rank" :style="rankStyle(i)">{{ i + 1 }}</span>{{ d.merchant }}</span>
          <span class="amount">
            {{ fmtMoney(d.amount) }}
            <small class="muted">· {{ d.count }}笔</small>
          </span>
        </li>
      </ol>
    </div>

    <AIReportModal :show="reportShow" @close="reportShow = false" />
  </section>
</template>

<style scoped>
.range-presets {
  display: flex;
  gap: var(--space-1);
}
.range-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
@media (max-width: 860px) {
  .range-presets {
    flex: 1 1 100%;
  }
  .range-presets .btn {
    flex: 1;
  }
  .range-label {
    flex: 1 1 100%;
  }
}
</style>
