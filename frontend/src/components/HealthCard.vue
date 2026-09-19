<script setup>
/* 财务健康评分（T-1.3）：储蓄率 / 负债率 / 应急金月数三分项加权。
 * 口径完全公开：每项的计算公式由后端随响应下发，原样展示不加工。 */
import { computed, onMounted, ref } from "vue";
import { statHealth } from "../api/stat";
import { isBusy, runTask } from "../composables/useLoading";
import { fmtMoney } from "../utils/format";
import AppIcon from "./AppIcon.vue";

const report = ref(null);
const expanded = ref(false);
const loading = computed(() => isBusy("health:load"));

const gradeClass = computed(() => {
  if (!report.value?.score) return "";
  if (report.value.score >= 80) return "grade-good";
  if (report.value.score >= 60) return "grade-fair";
  return "grade-warn";
});

function fmtValue(item) {
  if (item.value == null) return "—";
  if (item.unit === "个月") return item.value + " 个月";
  if (item.key === "savings" || item.key === "debt") return item.value + "%";
  return fmtMoney(item.value);
}

async function load() {
  await runTask({
    key: "health:load",
    title: "计算财务健康评分",
    mode: "latest",
    silent: true,
    rethrow: false,
    task: async () => {
      report.value = await statHealth();
    },
  });
}

onMounted(load);
</script>

<template>
  <div class="chart-box">
    <div class="section-head">
      <h3>财务健康</h3>
      <span class="section-head__hint">
        近 6 个完整月口径 · 分项公式完全公开，点「口径说明」展开
      </span>
    </div>

    <div v-if="report && report.score != null" class="health-body">
      <div class="health-score" :class="gradeClass">
        <span class="health-score__num">{{ report.score }}</span>
        <span class="health-score__grade">{{ report.grade }}</span>
      </div>
      <div class="health-items">
        <div v-for="i in report.items" :key="i.key" class="health-item">
          <div class="health-item__head">
            <span>{{ i.label }}</span>
            <span class="health-item__value">{{ fmtValue(i) }}</span>
            <span class="health-item__score">
              {{ i.score != null ? i.score + " 分" : "不计分" }}
            </span>
          </div>
          <span class="health-item__bar">
            <span class="health-item__fill" :style="{ width: (i.score || 0) + '%' }"></span>
          </span>
          <span v-if="!i.available" class="health-item__hint">{{ i.hint }}</span>
        </div>
      </div>
    </div>
    <div v-else-if="report" class="empty">
      暂无足够数据评分：需要近 6 个完整月的收支记录或资产快照
    </div>

    <button v-if="report" class="btn mini ghost" @click="expanded = !expanded">
      <AppIcon name="more" :size="13" /> {{ expanded ? "收起口径" : "口径说明" }}
    </button>
    <ul v-if="expanded && report" class="health-formulas">
      <li>评估窗口：{{ report.window.start }} ~ {{ report.window.end }}（近 {{ report.window.months }} 个完整月），
        月均收入 {{ fmtMoney(report.avg_income) }} / 月均支出 {{ fmtMoney(report.avg_expense) }}</li>
      <li v-for="i in report.items" :key="i.key">{{ i.label }}：{{ i.formula }}（权重 {{ i.weight * 100 }}%）</li>
      <li>总分 = 已计分分项的加权平均；缺数据的分项不计分，权重按剩余分项归一</li>
    </ul>
  </div>
</template>

<style scoped>
.health-body {
  display: flex;
  gap: 18px;
  align-items: center;
  flex-wrap: wrap;
}
.health-score {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-width: 86px;
}
.health-score__num {
  font-size: 34px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.health-score__grade {
  font-size: 13px;
}
.grade-good .health-score__num,
.grade-good .health-score__grade {
  color: #2e8b57;
}
.grade-fair .health-score__num {
  color: #e8833a;
}
.grade-warn .health-score__num,
.grade-warn .health-score__grade {
  color: #d64545;
}
.health-items {
  flex: 1;
  min-width: 220px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.health-item__head {
  display: flex;
  gap: 10px;
  font-size: 13px;
  align-items: baseline;
}
.health-item__value {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.health-item__score {
  margin-left: auto;
  opacity: 0.75;
}
.health-item__bar {
  display: block;
  height: 6px;
  border-radius: 3px;
  background: rgba(0, 0, 0, 0.08);
  overflow: hidden;
}
.health-item__fill {
  display: block;
  height: 100%;
  background: var(--accent, #e8833a);
}
.health-item__hint {
  font-size: 12px;
  opacity: 0.7;
}
.health-formulas {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: 12px;
  opacity: 0.8;
  line-height: 1.7;
}
</style>
