<script setup>
/* 现金流预测区（T-6.4）：未来 30/90 天余额曲线（P50 预期 + P90 悲观）。
 * 口径完全公开——起点余额来源、观察窗口、固定项逐条列出，可逐项排除后重算；
 * 数字全部由后端按确定规则计算，前端只展示 */
import { ref, watch } from "vue";
import { cashFlow } from "../api/forecast";
import { axisBase, chartBase, chartTokens } from "../utils/chartTheme";
import { fmtMoney, fmtType } from "../utils/format";
import { useChart } from "../composables/useChart";
import { runTask } from "../composables/useLoading";
import { store } from "../store";
import AppIcon from "./AppIcon.vue";

const horizon = ref(90);
const data = ref(null);
const excluded = ref([]);
const showCaliber = ref(false);
const chartEl = ref(null);
const chart = useChart(chartEl, (c) => renderChart(c), {
  canRender: () => store.tab === "dashboard",
});

const startSourceLabel = {
  asset_snapshot: "资产快照",
  bills_net: "全部流水净额",
};

const hasAnyHistory = () => {
  const d = data.value;
  return !!d && (d.fixed_items.length || d.excluded_items.length || d.variable.p50_monthly > 0);
};

async function load() {
  const res = await runTask({
    key: "forecast:load",
    title: "加载现金流预测",
    detail: "正在识别固定收支并推算余额曲线…",
    mode: "latest",
    rethrow: false,
    successText: "预测已更新",
    task: async (_update, isCurrent) => {
      let d;
      try {
        d = await cashFlow({ horizon: horizon.value, exclude: excluded.value });
      } catch (err) {
        throw new Error("预测加载失败：" + err.message);
      }
      /* latest：慢的旧响应后到时不覆盖新数据（同看板/预算区的约定） */
      if (!isCurrent()) return null;
      data.value = d;
      excluded.value = d.excluded_items.map((i) => i.key);
      return d;
    },
  });
  if (res) chart.render();
}

function toggleItem(item) {
  const key = item.key;
  const set = new Set(excluded.value);
  if (set.has(key)) set.delete(key);
  else set.add(key);
  excluded.value = [...set];
  load();
}

function renderChart(c) {
  const t = chartTokens();
  const axis = axisBase();
  const base = chartBase();
  const pts = data.value?.points || [];
  c.setOption(
    {
      ...base,
      legend: {
        data: ["预期余额 P50", "悲观余额 P90"],
        textStyle: { color: t.subtext, fontSize: 12 },
        icon: "roundRect",
        itemWidth: 10,
        itemHeight: 10,
        top: 0,
        right: 0,
      },
      xAxis: {
        type: "category",
        data: pts.map((p) => p.date),
        ...axis,
        splitLine: { show: false },
        axisLabel: {
          ...axis.axisLabel,
          formatter: (v) => v.slice(5), // 只显示 MM-DD，年份在口径区可查
        },
      },
      yAxis: {
        type: "value",
        ...axis,
        axisLine: { show: false },
        axisLabel: { formatter: (v) => "¥" + v, color: t.subtext, fontSize: 11 },
      },
      series: [
        {
          name: "预期余额 P50",
          type: "line",
          showSymbol: false,
          lineStyle: { width: 2.4, color: t.primary },
          itemStyle: { color: t.primary },
          data: pts.map((p) => p.p50),
        },
        {
          name: "悲观余额 P90",
          type: "line",
          showSymbol: false,
          lineStyle: { width: 1.8, type: "dashed", color: t.warn },
          itemStyle: { color: t.warn },
          data: pts.map((p) => p.p90),
        },
      ],
    },
    true,
  );
}

watch(horizon, load);
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
      <h3>现金流预测</h3>
      <div class="forecast-toolbar">
        <button
          v-for="h in [30, 90]"
          :key="h"
          class="btn mini"
          :class="{ primary: horizon === h }"
          @click="horizon = h"
        >
          {{ h }} 天
        </button>
        <button class="btn mini ghost" @click="showCaliber = !showCaliber">
          <AppIcon :name="showCaliber ? 'sort' : 'filter'" :size="14" />
          口径
        </button>
      </div>
      <span v-if="data" class="section-head__hint">
        起点 {{ fmtMoney(data.start_balance) }}（{{ startSourceLabel[data.start_source] || data.start_source }}）·
        预计 {{ horizon }} 天后 {{ fmtMoney(data.points[data.points.length - 1]?.p50) }}
      </span>
    </div>

    <div v-if="data && !hasAnyHistory()" class="forecast-empty">
      近 3 个完整月没有收支流水，暂无法预测——曲线仅为起点余额直线。
    </div>
    <div v-show="data && hasAnyHistory()" ref="chartEl" class="chart"></div>

    <!-- 口径区：怎么算出来的全部摊开，可逐项排除固定项后重算 -->
    <div v-if="data && showCaliber" class="forecast-caliber">
      <div class="caliber-block">
        <h4>观察窗口</h4>
        <p>
          {{ data.window.start }} 至 {{ data.window.end }}（{{ data.window.months.join("、") }}），
          可变支出 P50 {{ fmtMoney(data.variable.p50_monthly) }}/月、
          P90 {{ fmtMoney(data.variable.p90_monthly) }}/月（日均按
          {{ data.variable.days_per_month }} 天/月折算）
        </p>
      </div>
      <div class="caliber-block">
        <h4>固定收支（{{ data.fixed_items.length + data.excluded_items.length }} 项）</h4>
        <p v-if="!data.fixed_items.length && !data.excluded_items.length" class="empty">
          未识别到每月稳定出现的同商户收支
        </p>
        <ul class="fixed-list">
          <li v-for="item in data.fixed_items" :key="item.key">
            <span class="fixed-type" :class="item.tx_type === 'income' ? 'amount--income' : 'amount--expense'">
              {{ fmtType(item.tx_type) }}
            </span>
            <span class="fixed-name" :title="item.merchant">{{ item.merchant }}</span>
            <span class="fixed-meta">每月 {{ fmtMoney(item.monthly_amount) }} · {{ item.day_of_month }} 号</span>
            <button class="btn mini ghost" @click="toggleItem(item)">排除</button>
          </li>
          <li v-for="item in data.excluded_items" :key="item.key" class="fixed-excluded">
            <span class="fixed-type">{{ fmtType(item.tx_type) }}</span>
            <span class="fixed-name" :title="item.merchant">{{ item.merchant }}</span>
            <span class="fixed-meta">已排除，计入可变支出</span>
            <button class="btn mini" @click="toggleItem(item)">恢复</button>
          </li>
        </ul>
      </div>
      <div class="caliber-block">
        <h4>计算说明</h4>
        <ul class="note-list">
          <li v-for="(note, i) in data.notes" :key="i">{{ note }}</li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
.forecast-toolbar {
  display: flex;
  gap: var(--space-1);
  align-items: center;
}
.forecast-empty {
  padding: var(--space-3) 0;
  color: var(--color-text-tertiary);
  font-size: 0.9rem;
}
.forecast-caliber {
  margin-top: var(--space-2);
  padding: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: 10px;
  display: grid;
  gap: var(--space-2);
}
.caliber-block h4 {
  font-size: 0.85rem;
  margin-bottom: var(--space-1);
  color: var(--color-text-secondary);
}
.caliber-block p {
  font-size: 0.85rem;
  color: var(--color-text-secondary);
}
.fixed-list {
  display: grid;
  gap: var(--space-1);
}
.fixed-list li {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 0.88rem;
}
.fixed-type {
  font-size: 0.78rem;
  flex-shrink: 0;
}
.fixed-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fixed-meta {
  color: var(--color-text-tertiary);
  font-size: 0.8rem;
  flex: 1;
  white-space: nowrap;
}
.fixed-excluded .fixed-name,
.fixed-excluded .fixed-meta {
  color: var(--color-text-tertiary);
}
.note-list {
  padding-left: 1.2em;
  display: grid;
  gap: 2px;
}
.note-list li {
  font-size: 0.8rem;
  color: var(--color-text-tertiary);
}
@media (max-width: 640px) {
  .forecast-toolbar {
    flex: 1 1 100%;
  }
  .fixed-meta {
    flex: 0 1 auto;
  }
}
</style>
