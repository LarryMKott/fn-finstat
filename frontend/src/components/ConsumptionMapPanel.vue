<script setup>
/* 消费地图（城市气泡图）
 *
 * 设计取舍：账单里没有地区字段，地域只能从商户名/备注文本推断，所以这里刻意
 * 不画省界/国界 —— 既避开了地图边界合规问题（官方标准地图只有 JPG/EPS，
 * 改动需送审），也让视觉焦点落在「钱花在哪些城市」本身。
 *
 * 呈现三层信息：
 *   1. 气泡图 —— 城市中心点打点，半径与颜色映射支出金额，一眼看出消费集中度
 *   2. TOP 榜单 —— 与气泡一一对应的精确数字，弥补气泡无法读数的问题
 *   3. 识别率提示 —— 如实告知「有多少支出没能识别出地域」，不伪造完整分布
 */
import { computed, nextTick, ref, watch } from "vue";
import { regionMap } from "../api/stat";
import { chartBase, chartTokens, heatRamp } from "../utils/chartTheme";
import { fmtMoney } from "../utils/format";
import { presetWindow } from "../utils/datetime";
import { useChart } from "../composables/useChart";
import { isBusy, runTask } from "../composables/useLoading";
import { store } from "../store";
import AppIcon from "./AppIcon.vue";

const data = ref(null);
const chartEl = ref(null);

/* 时间范围：预设优先，与看板保持一致的交互心智 */
const range = ref("year");

const PRESETS = [
  { key: "month", label: "本月" },
  { key: "year", label: "本年" },
  { key: "all", label: "全部" },
];

const { render, resize } = useChart(chartEl, (chart) => renderChart(chart));

/* 预设 → 查询参数（"本月/本年"规则与看板共用 utils/datetime.presetWindow） */
function rangeQuery() {
  const { start, end } = presetWindow(range.value);
  const params = {};
  if (start) params.start = start;
  if (end) params.end = end;
  return params;
}

const cities = computed(() => data.value?.cities || []);
/* 只有带坐标的城市才能打点；缺失坐标的仍保留在榜单里 */
const plottable = computed(() => cities.value.filter((c) => Array.isArray(c.coord) && c.coord.length === 2));
const hasData = computed(() => plottable.value.length > 0);

const maxCityValue = computed(() => Math.max(0, ...cities.value.map((c) => c.value)));
const topCity = computed(() => cities.value[0] || null);

/* 切换时间范围是查询：用 latest 模式（新请求接管浮层显示），
 * 但 latest 只作废 HUD 状态、不会取消已在途的 Promise ——
 * 旧响应返回后仍会执行赋值覆盖新数据，所以这里必须留一层序号校验。 */
let loadSeq = 0;

async function load() {
  await runTask({
    key: "map:load",
    title: "加载消费地图",
    detail: "正在按城市汇总支出…",
    mode: "latest",
    rethrow: false,
    successText: (res) => `覆盖 ${res?.cities?.length ?? 0} 个城市`,
    task: async () => {
      const seq = ++loadSeq;
      let res;
      try {
        res = await regionMap(rangeQuery());
      } catch (err) {
        throw new Error("消费地图加载失败：" + err.message);
      }
      /* 已有更新的请求发出：本次响应作废，不写回也不渲染 */
      if (seq !== loadSeq) return null;
      data.value = res;
      /* 仅在有可打点数据时渲染：容器此时可能仍是 display:none，
       * 在隐藏容器上 init 会得到 0 尺寸实例，之后再无数据时就永远空白 */
      if (hasData.value) {
        await nextTick();
        render();
      }
      return data.value;
    },
  });
}

/* 半径映射：面积正比于金额（视觉上比半径正比更贴近直觉），并设最小半径保证小点可见 */
function radiusOf(value, max) {
  if (!max || max <= 0) return 6;
  const ratio = Math.max(0, Math.min(1, value / max));
  return 6 + 30 * Math.sqrt(ratio);
}

function renderChart(chart) {
  const t = chartTokens();
  const base = chartBase();
  const points = plottable.value;
  const max = maxCityValue.value;

  /* 坐标范围：以数据实际跨度为准，同时兜底到中国大致经纬度，防止单点或空数据时轴塌缩 */
  const lngs = points.map((c) => c.coord[0]);
  const lats = points.map((c) => c.coord[1]);
  const spanX = Math.max(...lngs, 136) - Math.min(...lngs, 73) || 1;
  const spanY = Math.max(...lats, 54) - Math.min(...lats, 3) || 1;

  chart.setOption(
    {
      ...base,
      tooltip: {
        ...base.tooltip,
        trigger: "item",
        formatter: (p) => {
          const d = p.data;
          return `<b>${d.name}</b><br/>${d.province}<br/>支出：${fmtMoney(d.value)}<br/>笔数：${d.count} 笔`;
        },
      },
      /* 直角坐标系承载经纬度：x = 经度、y = 纬度。
       * 刻意不用 geo 组件 —— ECharts 的 geo 必须 registerMap 提供边界数据，
       * 而使用行政边界会涉及地图合规送审；散点 + 网格轴同样能表达地域分布，
       * 且完全不需要任何边界数据。 */
      grid: { left: 52, right: 30, top: 26, bottom: 48, containLabel: false },
      xAxis: {
        type: "value",
        min: Math.min(...lngs, 73) - spanX * 0.04,
        max: Math.max(...lngs, 136) + spanX * 0.04,
        name: "经度",
        nameTextStyle: { color: t.subtext, fontSize: 11 },
        axisLine: { lineStyle: { color: t.splitLine } },
        axisTick: { show: false },
        axisLabel: { color: t.subtext, fontSize: 11, formatter: (v) => v.toFixed(0) + "°E" },
        splitLine: { lineStyle: { color: t.splitLine, type: "dashed" } },
      },
      yAxis: {
        type: "value",
        min: Math.min(...lats, 3) - spanY * 0.04,
        max: Math.max(...lats, 54) + spanY * 0.04,
        name: "纬度",
        nameTextStyle: { color: t.subtext, fontSize: 11 },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: t.subtext, fontSize: 11, formatter: (v) => v.toFixed(0) + "°N" },
        splitLine: { lineStyle: { color: t.splitLine, type: "dashed" } },
      },
      /* 用 visualMap 控制颜色深浅（金额越大越浓），半径由数据自身计算 */
      visualMap: {
        show: true,
        min: 0,
        max: max || 1,
        dimension: 2,
        seriesIndex: 0,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        itemWidth: 12,
        itemHeight: 100,
        text: ["高", "低"],
        textStyle: { color: t.subtext, fontSize: 11 },
        inRange: {
          /* 色阶统一走 chartTheme.heatRamp()：从 CSS 基础调色板逐阶取值，
             深浅方向随主题反转，避免此处硬编码与设计令牌脱节 */
          color: heatRamp(),
        },
      },
      series: [
        {
          type: "scatter",
          data: points.map((c) => ({
            name: c.name,
            value: [c.coord[0], c.coord[1], c.value],
            province: c.province,
            count: c.count,
          })),
          symbolSize: (val) => radiusOf(val[2], max),
          itemStyle: {
            opacity: 0.82,
            borderColor: t.surface,
            borderWidth: 1,
            shadowBlur: 8,
            shadowColor: "rgba(0,0,0,.18)",
          },
          emphasis: { itemStyle: { opacity: 1, borderWidth: 2 } },
          label: {
            show: true,
            position: "right",
            distance: 4,
            color: t.text,
            fontSize: 11,
            formatter: (p) => p.data.name,
          },
          labelLayout: { hideOverlap: true },
          z: 3,
        },
      ],
    },
    true,
  );
}

watch(range, load);
watch(
  () => store.tab === "map",
  (active) => {
    if (active) load();
  },
  { immediate: true },
);
/* 数据从无到有时容器刚从 display:none 变可见，已存在的实例需要 resize 一次 */
watch(hasData, (visible) => {
  if (visible) nextTick(() => resize());
});
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'map' }">
    <div class="filter-bar">
      <div class="range-presets">
        <button
          v-for="p in PRESETS"
          :key="p.key"
          class="btn mini"
          :class="{ primary: range === p.key }"
          @click="range = p.key"
        >
          {{ p.label }}
        </button>
      </div>
      <span class="sep">|</span>
      <span class="range-label hint">
        <template v-if="topCity">消费最集中的城市：{{ topCity.name }}（{{ fmtMoney(topCity.value) }}）</template>
        <template v-else>暂无支出数据</template>
      </span>
      <button class="btn right" :disabled="isBusy('map:load')" @click="load">
        <AppIcon name="restore" :size="15" />
        {{ isBusy('map:load') ? "加载中…" : "刷新" }}
      </button>
    </div>

    <div class="map-layout">
      <div class="chart-box">
        <div class="section-head">
          <h3>城市消费气泡图</h3>
          <span class="section-head__hint">气泡越大、颜色越深表示该城市支出越高，可滚轮缩放拖动查看</span>
        </div>
        <div v-show="hasData" ref="chartEl" class="chart map-chart"></div>
        <div v-if="!hasData && !isBusy('map:load')" class="empty">
          当前范围内没有可定位到城市的消费记录
        </div>
      </div>

      <div class="chart-box map-side">
        <div class="section-head">
          <h3>城市消费 TOP</h3>
        </div>
        <ol class="city-rank">
          <li v-if="!cities.length" class="empty">暂无数据</li>
          <li v-for="(c, i) in cities" :key="c.name + '-' + i">
            <span class="city-rank__idx">{{ i + 1 }}</span>
            <span class="city-rank__main">
              <span class="city-rank__name">{{ c.name }}</span>
              <span class="city-rank__prov">{{ c.province }}</span>
            </span>
            <span class="city-rank__amount">
              {{ fmtMoney(c.value) }}
              <small class="muted">· {{ c.count }}笔</small>
            </span>
          </li>
        </ol>
      </div>
    </div>

    <!-- 识别率说明：诚实披露数据局限，避免地图被误读为完整地理分布 -->
    <div v-if="data" class="chart-box map-note">
      <div class="section-head">
        <h3>识别说明</h3>
        <span class="section-head__hint">账单不含地区字段，地域由商户名与备注推断</span>
      </div>
      <div class="note-grid">
        <div class="note-item">
          <span class="note-item__label">识别率（按金额）</span>
          <span class="note-item__value">{{ data.matched_rate }}%</span>
        </div>
        <div class="note-item">
          <span class="note-item__label">已识别支出</span>
          <span class="note-item__value">{{ fmtMoney(data.matched_amount) }}</span>
        </div>
        <div class="note-item">
          <span class="note-item__label">未识别支出</span>
          <span class="note-item__value muted">{{ fmtMoney(data.total_amount - data.matched_amount) }}</span>
        </div>
        <div class="note-item">
          <span class="note-item__label">识别笔数</span>
          <span class="note-item__value">{{ data.matched_count }} / {{ data.scanned_count }}</span>
        </div>
      </div>
      <p class="hint">
        未识别部分多为纯线上消费（如外卖、会员充值等不含地域线索的商户），不代表这些支出没有发生地点。
        若希望提高识别率，可在导入后为流水补充备注（如「成都春熙路店」）。
      </p>
      <p v-if="data.truncated" class="hint">
        流水较多，本次仅统计金额最大的 {{ data.scanned_count }} 条（共 {{ data.total_count }} 条）。
      </p>
    </div>
  </section>
</template>

<style scoped>
/* 左图右榜：气泡图需要较大画布，榜单固定窄列，窄屏下沉为上下结构 */
.map-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 320px;
  gap: var(--space-4);
  margin-bottom: var(--space-4);
  align-items: start;
}
.map-layout .chart-box {
  margin-bottom: 0;
}
.map-chart {
  height: 460px;
}
.map-side {
  max-height: 552px;
  overflow-y: auto;
}

/* 城市榜：序号 + 城市/省份 + 金额三栏，金额右对齐便于纵向比较 */
.city-rank {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.city-rank li {
  display: flex;
  align-items: center;
  gap: var(--space-2-5);
  padding: var(--space-2) var(--space-2-5);
  border-radius: var(--radius-sm);
  transition: background var(--dur-fast) var(--ease-out);
}
.city-rank li:hover {
  background: var(--color-surface-sunken, rgba(0, 0, 0, 0.03));
}
.city-rank__idx {
  flex-shrink: 0;
  width: 20px;
  text-align: center;
  font-family: var(--font-numeric);
  font-size: var(--text-xs);
  font-weight: 620;
  color: var(--color-text-tertiary);
}
.city-rank__main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.city-rank__name {
  font-weight: 600;
  font-size: var(--text-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.city-rank__prov {
  font-size: var(--text-2xs);
  color: var(--color-text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.city-rank__amount {
  flex-shrink: 0;
  text-align: right;
  font-family: var(--font-numeric);
  font-weight: 620;
  font-size: var(--text-sm);
}
.city-rank__amount small {
  font-family: var(--font-sans);
  font-weight: 400;
}

/* 识别说明：数字卡片栅格 */
.note-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}
.note-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.note-item__label {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}
.note-item__value {
  font-family: var(--font-numeric);
  font-size: var(--text-lg);
  font-weight: 650;
}
.map-note .hint {
  margin: 0 0 var(--space-1);
}

@media (max-width: 1180px) {
  .map-layout {
    grid-template-columns: 1fr;
  }
  .map-side {
    max-height: none;
  }
}
@media (max-width: 860px) {
  .map-chart {
    height: 340px;
  }
  .note-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
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
