<script setup>
/* 现金流预测区（T-6.4）：未来 30/90 天余额曲线（P50 预期 + P90 悲观）。
 * 口径完全公开——起点余额来源、观察窗口、固定项逐条列出，可逐项排除后重算；
 * 数字全部由后端按确定规则计算，前端只展示 */
import { computed, ref, watch } from "vue";
import { cashFlow, expenseStructure, subscriptions as fetchSubscriptions } from "../api/forecast";
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

/* 支出结构拆分（T-1.5）：必选项（固定）/ 可砍项（弹性），随预测同窗口加载 */
const structure = ref(null);

/* 订阅侦探（AI-5）：时间线 / 涨价 / 僵尸订阅，随预测同窗口加载 */
const subs = ref(null);
const subStepPct = computed(() =>
  subs.value ? Math.round((subs.value.caliber.price_step_factor - 1) * 100) : 0,
);

async function loadSubscriptions() {
  await runTask({
    key: "forecast:subs",
    title: "分析订阅",
    mode: "latest",
    silent: true,
    rethrow: false,
    task: async () => {
      subs.value = await fetchSubscriptions();
    },
  });
}

const startSourceLabel = {
  asset_snapshot: "资产快照",
  bills_net: "全部流水净额",
};

const hasAnyHistory = () => {
  const d = data.value;
  return !!d && (d.fixed_items.length || d.excluded_items.length || d.variable.p50_monthly > 0);
};

async function loadStructure() {
  await runTask({
    key: "forecast:structure",
    title: "拆分支出结构",
    mode: "latest",
    silent: true,
    rethrow: false,
    task: async () => {
      structure.value = await expenseStructure();
    },
  });
}

async function load() {
  loadStructure();
  loadSubscriptions();
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

    <!-- 支出结构拆分（T-1.5）：必选项 / 可砍项 -->
    <div v-if="structure && structure.total_monthly > 0" class="structure-block">
      <div class="structure-head">
        <h4>
          支出结构（{{ structure.window.start }} ~ {{ structure.window.end }} 月均
          {{ fmtMoney(structure.total_monthly) }}）
        </h4>
        <span class="structure-pct">
          必选项占 {{ structure.fixed_pct == null ? "—" : structure.fixed_pct + "%" }}
        </span>
      </div>
      <div class="structure-bars">
        <div class="structure-bar">
          <span class="structure-bar__label">必选项 {{ fmtMoney(structure.fixed_monthly) }}/月</span>
          <span class="structure-bar__track">
            <span class="structure-bar__fill fixed" :style="{ width: (structure.fixed_pct || 0) + '%' }"></span>
          </span>
          <span class="structure-bar__count">{{ structure.fixed.length }} 项</span>
        </div>
        <div class="structure-bar">
          <span class="structure-bar__label">可砍项 {{ fmtMoney(structure.flexible_monthly) }}/月</span>
          <span class="structure-bar__track">
            <span class="structure-bar__fill flexible" :style="{ width: structure.fixed_pct == null ? 0 : (100 - structure.fixed_pct) + '%' }"></span>
          </span>
          <span class="structure-bar__count">{{ structure.flexible.length }} 项</span>
        </div>
      </div>
      <div class="structure-lists">
        <div class="structure-col">
          <h5>必选项（每月固定支出）</h5>
          <div v-if="!structure.fixed.length" class="empty">未识别到每月稳定出现的支出</div>
          <div v-for="f in structure.fixed" :key="f.merchant" class="structure-row">
            <span class="structure-row__name" :title="f.merchant">{{ f.merchant }}</span>
            <span class="structure-row__num">{{ fmtMoney(f.monthly_amount) }}/月</span>
          </div>
        </div>
        <div class="structure-col">
          <h5>可砍项（弹性支出，按月均降序）</h5>
          <div v-if="!structure.flexible.length" class="empty">没有弹性支出</div>
          <div v-for="f in structure.flexible.slice(0, 8)" :key="f.merchant" class="structure-row">
            <span class="structure-row__name" :title="f.merchant">{{ f.merchant }}</span>
            <span class="structure-row__num">{{ fmtMoney(f.monthly_amount) }}/月 · {{ f.count }} 笔</span>
          </div>
          <div v-if="structure.flexible.length > 8" class="empty">
            还有 {{ structure.flexible.length - 8 }} 项小额支出未列出
          </div>
        </div>
      </div>
      <p class="structure-note">
        判定口径：近 {{ structure.window.months }} 个完整月每月出现且月度合计波动 ≤ {{ structure.band_pct }}% 的同商户支出记为必选项
      </p>
    </div>

    <!-- 订阅侦探（AI-5）：时间线 / 台阶涨价 / 疑似僵尸订阅 -->
    <div v-if="subs && subs.subscriptions.length" class="structure-block">
      <div class="structure-head">
        <h4>
          订阅侦探（{{ subs.window.start }} ~ {{ subs.window.end }}）
        </h4>
        <span class="structure-pct">
          {{ subs.subscriptions.length }} 项 · 月合计 {{ fmtMoney(subs.monthly_total) }}
          <template v-if="subs.income_pct != null"> · 占收入 {{ subs.income_pct }}%</template>
        </span>
      </div>
      <div class="sub-list">
        <div v-for="s in subs.subscriptions" :key="s.merchant" class="sub-row">
          <span class="sub-row__name" :title="s.merchant">{{ s.merchant }}</span>
          <span
            v-if="s.price_step"
            class="sub-flag sub-flag--up"
            :title="`自 ${s.price_step.since} 起 ${fmtMoney(s.price_step.from)} → ${fmtMoney(s.price_step.to)}/月`"
          >
            涨价 +{{ s.price_step.pct }}%
          </span>
          <span
            v-if="s.zombie"
            class="sub-flag sub-flag--old"
            title="近一年每月都扣费且金额几乎不变，建议复核是否还需要"
          >
            僵尸?
          </span>
          <span class="structure-row__num">
            {{ fmtMoney(s.monthly_amount) }}/月 · 连续 {{ s.streak_months }} 个月
          </span>
        </div>
      </div>
      <p class="structure-note">
        判定口径：近 {{ subs.window.months }} 个完整月同商户支出出现 ≥ {{ subs.caliber.min_months }} 个月即按订阅分析；
        月费跳升 ≥ {{ subStepPct }}% 且不再回落记为涨价；
        整个窗口每月扣费且波动 ≤ {{ subs.caliber.band_pct }}% 记为疑似僵尸订阅
      </p>
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
  .structure-block {
    margin-top: 12px;
    border-top: 1px dashed rgba(0, 0, 0, 0.12);
    padding-top: 10px;
  }
  .structure-head {
    display: flex;
    gap: 10px;
    align-items: baseline;
    flex-wrap: wrap;
  }
  .structure-head h4 {
    margin: 0;
    font-size: 14px;
  }
  .structure-pct {
    font-size: 13px;
    opacity: 0.8;
  }
  .structure-bars {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin: 8px 0;
  }
  .structure-bar {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
  }
  .structure-bar__label {
    min-width: 12em;
  }
  .structure-bar__track {
    flex: 1;
    max-width: 320px;
    height: 8px;
    border-radius: 4px;
    background: rgba(0, 0, 0, 0.08);
    overflow: hidden;
    display: inline-block;
  }
  .structure-bar__fill {
    display: block;
    height: 100%;
  }
  .structure-bar__fill.fixed {
    background: #d64545;
  }
  .structure-bar__fill.flexible {
    background: var(--accent, #e8833a);
  }
  .structure-bar__count {
    opacity: 0.7;
  }
  .structure-lists {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }
  @media (max-width: 640px) {
    .structure-lists {
      grid-template-columns: 1fr;
    }
  }
  .structure-col h5 {
    margin: 0 0 6px;
    font-size: 13px;
    opacity: 0.8;
  }
  .structure-row {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    font-size: 13px;
    padding: 2px 0;
  }
  .structure-row__name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .structure-row__num {
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .structure-note {
    margin: 8px 0 0;
    font-size: 12px;
    opacity: 0.7;
  }
  .sub-list {
    display: grid;
    gap: 2px;
    margin: 8px 0;
  }
  .sub-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    padding: 2px 0;
  }
  .sub-row__name {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .sub-flag {
    flex-shrink: 0;
    font-size: 11px;
    line-height: 1.4;
    padding: 0 6px;
    border-radius: 8px;
    white-space: nowrap;
    cursor: help;
  }
  .sub-flag--up {
    color: #d64545;
    background: rgba(214, 69, 69, 0.12);
  }
  .sub-flag--old {
    color: var(--color-text-secondary);
    background: var(--color-surface-sunken);
  }
</style>
