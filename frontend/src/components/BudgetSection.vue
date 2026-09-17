<script setup>
/* 预算进度区：当月预算 vs 实际支出，支持增删改（总预算 = category 为空的行）。
 * 改造点：进度条加状态色语义（正常/接近/超支），数字用等宽字体对齐，
 * 剩余额度直接给出，省去用户心算 */
import { computed, ref, watch } from "vue";
import { budgetOverview, deleteBudget, upsertBudget } from "../api/budget";
import { budgetSuggestions } from "../api/forecast";
import { fmtMoney } from "../utils/format";
import { currentMonth } from "../utils/datetime";
import { confirm } from "../composables/useConfirm";
import { isBusy, runTask } from "../composables/useLoading";
import { categories, store } from "../store";
import { toast } from "../toast";
import AppIcon from "./AppIcon.vue";

const month = ref(currentMonth());
const overview = ref(null);
const form = ref({ category: "", amount: "", editingId: null });
const suggestions = ref(null);

const items = computed(() => overview.value?.items || []);

/* 汇总：已用额度占比，作为区块级健康度指标。
 * 设了总预算行时以其为准（其 expense 已是当月全部支出），
 * 否则汇总各分类预算行——绝不能两者混加，否则分子分母双重复计算 */
const totalUsed = computed(() => {
  const list = items.value;
  if (!list.length) return null;
  const overall = list.find((i) => !i.category);
  const rows = overall ? [overall] : list;
  const budget = rows.reduce((s, i) => s + Number(i.budget || 0), 0);
  const expense = rows.reduce((s, i) => s + Number(i.expense || 0), 0);
  return { budget, expense, pct: budget ? Math.min(999, Math.round((expense / budget) * 100)) : 0 };
});

/* 保存/删除期间锁住按钮，避免连点产生重复预算行 */
const busy = computed(() => isBusy("budget:submit") || isBusy("budget:delete"));

async function load() {
  await runTask({
    key: "budget:load",
    title: "加载预算",
    detail: `正在读取 ${month.value} 的预算…`,
    mode: "latest",
    rethrow: false,
    successText: "预算已更新",
    task: async (_update, isCurrent) => {
      let data;
      try {
        data = await budgetOverview(month.value);
      } catch (err) {
        throw new Error("预算加载失败：" + err.message);
      }
      /* latest 只作废浮层状态、不取消在途 Promise：快速切换月份时慢的旧响应
       * 会后到，已被新请求接管即作废，不得覆盖新数据 */
      if (!isCurrent()) return null;
      overview.value = data;
      return data;
    },
  });
}

function pct(item) {
  if (!item.budget) return 0;
  return Math.min(100, Math.round((item.expense / item.budget) * 100));
}

function barClass(item) {
  if (item.budget && item.expense > item.budget) return "over";
  if (pct(item) >= 80) return "warn";
  return "";
}

function remainLabel(item) {
  if (!item.budget) return "";
  const diff = Number(item.budget) - Number(item.expense);
  return diff >= 0 ? `剩 ${fmtMoney(diff)}` : `超 ${fmtMoney(-diff)}`;
}

function edit(item) {
  form.value = { category: item.category, amount: item.budget, editingId: item.id };
}

function cancelEdit() {
  form.value = { category: "", amount: "", editingId: null };
}

async function submit() {
  const amount = Number(form.value.amount);
  if (!isFinite(amount) || amount <= 0) {
    toast("请输入有效预算金额", true);
    return;
  }
  const addingTotal = form.value.editingId == null && !form.value.category;
  if (addingTotal && items.value.some((i) => i.category === "")) {
    toast("总预算已存在，如需修改请点击总预算行的「编辑」", true);
    return;
  }
  const res = await runTask({
    key: "budget:submit",
    title: form.value.editingId ? "更新预算" : "新增预算",
    detail: `正在保存「${form.value.category || "总预算"}」…`,
    rethrow: false,
    successText: "预算已保存",
    task: () => upsertBudget({ month: month.value, category: form.value.category, amount }),
  });
  if (!res) return;
  cancelEdit();
  load();
}

/* 智能建议（T-6.4）：近 6 个月分类中位数（剔除一次性大额）给出建议区间；
 * 只读接口，采纳 = 调既有预算 upsert，采纳后仍可走「编辑」手动微调 */
async function loadSuggestions() {
  const res = await runTask({
    key: "forecast:suggest",
    title: "生成预算建议",
    detail: `正在分析 ${month.value} 前 6 个月的支出…`,
    mode: "latest",
    rethrow: false,
    successText: "建议已生成",
    task: async (_update, isCurrent) => {
      let data;
      try {
        data = await budgetSuggestions(month.value);
      } catch (err) {
        throw new Error("建议生成失败：" + err.message);
      }
      if (!isCurrent()) return null;
      return data;
    },
  });
  if (res) suggestions.value = res;
}

function toggleSuggestions() {
  if (suggestions.value) {
    suggestions.value = null;
    return;
  }
  loadSuggestions();
}

async function adoptSuggestion(s) {
  const res = await runTask({
    key: "budget:submit",
    title: "采纳预算建议",
    detail: `正在为「${s.category}」设置预算…`,
    rethrow: false,
    successText: `已采纳：${s.category} ${fmtMoney(s.suggested)}`,
    task: () =>
      upsertBudget({ month: month.value, category: s.category, amount: s.suggested }),
  });
  if (!res) return;
  load();
  loadSuggestions(); // 刷新 current_budget，已采纳的行不再显示「采纳」
}

async function remove(item) {
  const label = item.category || "总预算";
  const okToDelete = await confirm({
    title: "删除预算",
    message: `删除 ${month.value} 的「${label}」预算吗？`,
    danger: true,
    confirmText: "删除",
  });
  if (!okToDelete) return;
  /* DELETE 返回 204，runTask 会解析成 null；用 done 标记成功，否则删完不刷新 */
  let done = false;
  await runTask({
    key: "budget:delete",
    title: "删除预算",
    detail: `正在删除「${label}」…`,
    rethrow: false,
    successText: "预算已删除",
    task: async () => {
      await deleteBudget(item.id);
      done = true;
      return { deleted: true };
    },
  });
  if (done) load();
}

watch(month, () => {
  suggestions.value = null; // 换月后旧建议口径失效，需重新生成
  load();
});
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
      <h3>预算进度</h3>
      <div class="budget-toolbar">
        <input v-model="month" type="month" aria-label="预算月份" />
        <button
          class="btn mini"
          :class="{ ghost: suggestions }"
          :disabled="isBusy('forecast:suggest')"
          title="按近 6 个月分类支出中位数生成建议（剔除一次性大额）"
          @click="toggleSuggestions"
        >
          <AppIcon name="sparkles" :size="14" />
          {{ suggestions ? "收起建议" : "智能建议" }}
        </button>
        <div v-if="form.editingId == null" class="budget-add">
          <select v-model="form.category" title="选择分类，不选即总预算" aria-label="预算分类">
            <option value="">总预算</option>
            <option v-for="c in categories" :key="c.id" :value="c.name">{{ c.name }}</option>
          </select>
          <input v-model="form.amount" type="number" step="0.01" min="1" placeholder="预算金额" aria-label="预算金额" @keydown.enter="submit" />
          <button class="btn primary" :disabled="busy" @click="submit">
            <AppIcon name="plus" :size="15" /> 添加
          </button>
        </div>
      </div>
      <span v-if="totalUsed" class="section-head__hint">
        已用 {{ fmtMoney(totalUsed.expense) }} / {{ fmtMoney(totalUsed.budget) }} · {{ totalUsed.pct }}%
      </span>
    </div>

    <!-- 建议面板：口径摊开（窗口/剔除说明），一键采纳后可走上方编辑微调 -->
    <div v-if="suggestions" class="suggest-panel">
      <div class="suggest-head">
        <span>{{ suggestions.window.start }} ~ {{ suggestions.window.end }} 支出分析</span>
        <span class="suggest-hint">建议区间为中位数 × 0.9 ~ × 1.1，采纳后可编辑微调</span>
      </div>
      <div v-if="!suggestions.suggestions.length" class="empty">
        近 6 个月没有出现 ≥ 3 个月的分类支出，暂无建议
      </div>
      <div v-for="s in suggestions.suggestions" :key="s.category" class="suggest-row">
        <span class="suggest-name">{{ s.category }}</span>
        <span class="suggest-amount">{{ fmtMoney(s.suggested) }}</span>
        <span class="suggest-range hint">{{ fmtMoney(s.low) }} ~ {{ fmtMoney(s.high) }}</span>
        <span class="suggest-meta hint">
          {{ s.months_used }} 个月中位数
          <template v-if="s.excluded_outliers.length">
            · 已剔除 {{ s.excluded_outliers.length }} 笔大额
          </template>
          <template v-if="s.current_budget != null">· 已设 {{ fmtMoney(s.current_budget) }}</template>
        </span>
        <button
          v-if="s.current_budget == null || s.current_budget !== s.suggested"
          class="btn mini primary"
          :disabled="busy"
          @click="adoptSuggestion(s)"
        >
          采纳
        </button>
      </div>
    </div>

    <ul class="budget-list">
      <li v-if="!items.length" class="empty budget-empty">
        还没设置 {{ month }} 的预算，添加后这里会显示进度
      </li>
      <li v-for="item in items" :key="item.id">
        <template v-if="form.editingId === item.id">
          <div class="budget-edit-row">
            <input v-model="form.amount" type="number" step="0.01" min="1" aria-label="预算金额" @keydown.enter="submit" />
            <button class="btn mini primary" :disabled="busy" @click="submit">保存</button>
            <button class="btn mini ghost" @click="cancelEdit">取消</button>
          </div>
        </template>
        <template v-else>
          <div class="budget-head">
            <span class="budget-name">{{ item.category || "总预算" }}</span>
            <span class="budget-nums">
              {{ fmtMoney(item.expense) }} / {{ fmtMoney(item.budget) }}
              <em v-if="item.expense > item.budget">已超支</em>
              <span v-else class="budget-remain">{{ remainLabel(item) }}</span>
            </span>
            <span class="cat-actions">
              <button class="btn link" @click="edit(item)">编辑</button>
              <button class="btn link danger" :disabled="busy" @click="remove(item)">删除</button>
            </span>
          </div>
          <div
            class="budget-track"
            role="progressbar"
            :aria-valuenow="pct(item)"
            aria-valuemin="0"
            aria-valuemax="100"
            :aria-label="`${item.category || '总预算'} 已用 ${pct(item)}%`"
          >
            <div class="budget-fill" :class="barClass(item)" :style="{ width: pct(item) + '%' }"></div>
          </div>
        </template>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.budget-toolbar {
  margin-bottom: 0;
}
.section-head__hint {
  flex: 1 1 100%;
  order: 3;
}
.suggest-panel {
  margin: var(--space-2) 0;
  padding: var(--space-2);
  border: 1px dashed var(--color-border);
  border-radius: 10px;
  display: grid;
  gap: var(--space-1);
}
.suggest-head {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1) var(--space-2);
  font-size: 0.82rem;
  color: var(--color-text-secondary);
}
.suggest-hint {
  color: var(--color-text-tertiary);
  font-size: 0.78rem;
}
.suggest-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 0.88rem;
}
.suggest-name {
  min-width: 4em;
}
.suggest-amount {
  font-family: var(--font-numeric);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.suggest-range {
  white-space: nowrap;
}
.suggest-meta {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.budget-remain {
  margin-left: var(--space-1-5);
  color: var(--color-text-tertiary);
}
@media (max-width: 640px) {
  .budget-toolbar {
    flex: 1 1 100%;
  }
  .budget-add {
    width: 100%;
  }
  .suggest-row {
    flex-wrap: wrap;
  }
  .suggest-meta {
    flex: 1 1 100%;
    order: 5;
  }
}
</style>
