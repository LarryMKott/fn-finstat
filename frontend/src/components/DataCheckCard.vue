<script setup>
/* 记账数据体检（AI-2）：未分类 / 空商户 / 零金额 / 未记账月份 / 疑似重复导入。
 * 脏数据是 AI 归类与报表的主要噪声源——卡片只在有问题时渲染；
 * 「去处理」经 T-6.2 的 billsFilterHandoff 交接机制跳到流水页对应筛选。 */
import { computed, onMounted, ref } from "vue";
import { dataCheck } from "../api/stat";
import { fmtMoney } from "../utils/format";
import { runTask } from "../composables/useLoading";
import { billsFilterHandoff, store } from "../store";

const data = ref(null);
const problems = computed(() =>
  (data.value?.items || []).filter((i) => i.count > 0 && i.key !== "duplicates"),
);
const duplicates = computed(() => {
  const item = (data.value?.items || []).find((i) => i.key === "duplicates");
  return item && item.count > 0 ? item : null;
});

/* 交接口径与 AI 报告追溯/问账「存为筛选」同构（读后清空由流水页负责） */
function applyJump(jump) {
  billsFilterHandoff.value = {
    start: jump.start || "",
    end: jump.end || "",
    tx_type: jump.tx_type || "",
    categories: jump.categories || [],
    merchants: jump.merchants || [],
  };
  store.tab = "bills";
}

async function load() {
  await runTask({
    key: "datacheck:load",
    title: "数据体检",
    mode: "latest",
    silent: true,
    rethrow: false,
    task: async () => {
      data.value = await dataCheck();
    },
  });
}

onMounted(load);
</script>

<template>
  <div v-if="problems.length || duplicates" class="chart-box">
    <div class="section-head">
      <h3>数据体检</h3>
      <span class="section-head__hint">
        {{ data.problem_count }} 项待处理 · 脏数据会影响分类与报表，点「去处理」直接筛选
      </span>
    </div>
    <div class="check-list">
      <div v-for="item in problems" :key="item.key" class="check-row">
        <span class="check-row__label">{{ item.label }}</span>
        <span class="check-row__count">{{ item.count }}</span>
        <span class="check-row__hint" :title="item.hint">{{ item.hint }}</span>
        <button v-if="item.jump" class="btn mini" @click="applyJump(item.jump)">
          去处理
        </button>
      </div>
      <template v-if="duplicates">
        <div class="check-row">
          <span class="check-row__label">{{ duplicates.label }}</span>
          <span class="check-row__count">{{ duplicates.count }} 组</span>
          <span class="check-row__hint">{{ duplicates.hint }}</span>
        </div>
        <div v-for="(g, i) in duplicates.detail" :key="'dup-' + i" class="check-row check-row--sub">
          <span class="check-row__label" :title="g.merchant">{{ g.merchant }}</span>
          <span class="check-row__count">{{ g.count }} 条</span>
          <span class="check-row__hint">{{ g.day }} · {{ fmtMoney(g.amount) }} 元</span>
          <button class="btn mini" @click="applyJump(g.jump)">去处理</button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.check-list {
  display: grid;
  gap: 6px;
}
.check-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}
.check-row--sub {
  padding-left: 12px;
  opacity: 0.9;
}
.check-row__label {
  flex-shrink: 0;
  max-width: 12em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.check-row__count {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: #d64545;
}
.check-row__hint {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  opacity: 0.7;
}
@media (max-width: 640px) {
  .check-row {
    flex-wrap: wrap;
  }
  .check-row__hint {
    flex: 1 1 100%;
  }
}
</style>
