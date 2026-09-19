<script setup>
/* 家庭空间（T-7.2）：创建/凭码加入家庭、成员管理、月度聚合汇总、隐私开关。
 * 边界与后端一致：家庭页只展示聚合值（各成员之和，逐成员可核对）；
 * 成员明细默认互不可见，allow_detail_view 开启后才出现「看流水」入口。 */
import { computed, reactive, ref, watch } from "vue";
import { store, categories } from "../store";
import {
  createFamily,
  deleteFamilyBudget,
  disbandFamily,
  familyBudgetOverview,
  familyMemberBills,
  familySummary,
  getFamily,
  joinFamily,
  leaveFamily,
  regenerateInviteCode,
  removeFamilyMember,
  updateFamilySettings,
  upsertFamilyBudget,
} from "../api/family";
import { confirm } from "../composables/useConfirm";
import { isBusy, runTask } from "../composables/useLoading";
import { fmtMoney } from "../utils/format";
import { toast } from "../toast";
import AppIcon from "./AppIcon.vue";

const info = ref(null); // null = 未加载，false = 未加入任何家庭
const loaded = ref(false);
const summary = ref(null);
const month = ref(new Date().toISOString().slice(0, 7)); // YYYY-MM
const createName = ref("");
const joinCode = ref("");
const detailTarget = ref(null); // 正在看明细的成员
const detailRows = ref([]);
const detailTotal = ref(0);

const isAdmin = computed(() => info.value?.my_role === "admin");
const creating = computed(() => isBusy("family:create"));
const joining = computed(() => isBusy("family:join"));

/* 显示名：昵称快照优先，空则回退 user_id（本地部署 user_id 也可能为空） */
function displayName(m) {
  return m.nickname || m.user_id || "成员";
}

async function load(silent = true) {
  await runTask({
    key: "family:load",
    title: "加载家庭信息",
    mode: "latest",
    silent,
    rethrow: false,
    task: async () => {
      const data = await getFamily();
      info.value = data || false;
      loaded.value = true;
      if (data) {
        await loadSummary();
        await loadBudgets();
      }
      return data;
    },
  });
}

async function loadSummary() {
  const data = await familySummary(month.value);
  summary.value = data;
}

async function doCreate() {
  const name = createName.value.trim();
  if (!name) {
    toast("请输入家庭名称", true);
    return;
  }
  await runTask({
    key: "family:create",
    title: "创建家庭",
    rethrow: false,
    successText: "家庭已创建，把邀请码分享给家人吧",
    task: async () => {
      await createFamily(name);
      createName.value = "";
      await load();
    },
  });
}

async function doJoin() {
  const code = joinCode.value.trim().toUpperCase();
  if (!code) {
    toast("请输入邀请码", true);
    return;
  }
  await runTask({
    key: "family:join",
    title: "加入家庭",
    rethrow: false,
    task: async () => {
      const res = await joinFamily(code);
      joinCode.value = "";
      toast(`已加入「${res.family_name}」`);
      await load();
    },
  });
}

async function doLeave() {
  const ok = await confirm({
    title: "退出家庭",
    message: "退出后将看不到家庭汇总，自己的账单数据不受影响。确定退出吗？",
    danger: true,
    confirmText: "退出",
  });
  if (!ok) return;
  await runTask({
    key: "family:leave",
    title: "退出家庭",
    rethrow: false,
    successText: "已退出家庭",
    task: async () => {
      await leaveFamily();
      info.value = false;
      summary.value = null;
      budget.value = null;
    },
  });
}

async function doDisband() {
  const ok = await confirm({
    title: "解散家庭",
    message: "解散后所有成员恢复独立记账，各自的数据不受影响。确定解散吗？",
    danger: true,
    confirmText: "解散",
  });
  if (!ok) return;
  await runTask({
    key: "family:disband",
    title: "解散家庭",
    rethrow: false,
    successText: "家庭已解散",
    task: async () => {
      await disbandFamily();
      info.value = false;
      summary.value = null;
      budget.value = null;
    },
  });
}

async function doRemove(m) {
  const ok = await confirm({
    title: "移除成员",
    message: `确定把「${displayName(m)}」移出家庭吗？其账单数据不受影响。`,
    danger: true,
    confirmText: "移除",
  });
  if (!ok) return;
  await runTask({
    key: "family:remove",
    title: "移除成员",
    rethrow: false,
    successText: "已移除成员",
    task: async () => {
      await removeFamilyMember(m.user_id);
      await load();
    },
  });
}

async function doRegenerate() {
  await runTask({
    key: "family:regen",
    title: "重新生成邀请码",
    rethrow: false,
    successText: "已生成新邀请码，旧码已失效",
    task: async () => {
      await regenerateInviteCode();
      await load();
    },
  });
}

async function toggleDetail(e) {
  const value = e.target.checked;
  await runTask({
    key: "family:settings",
    title: "更新隐私设置",
    rethrow: false,
    successText: value ? "已开放成员明细互看" : "已关闭成员明细互看",
    task: async () => {
      const res = await updateFamilySettings(value);
      if (info.value) info.value.allow_detail_view = res.allow_detail_view;
      if (!value) {
        detailTarget.value = null;
        detailRows.value = [];
      }
    },
  });
  e.target.checked = info.value?.allow_detail_view ?? false;
}

async function showDetail(m) {
  if (detailTarget.value?.user_id === m.user_id) {
    detailTarget.value = null;
    detailRows.value = [];
    return;
  }
  await runTask({
    key: "family:detail",
    title: "加载成员流水",
    mode: "latest",
    silent: true,
    rethrow: false,
    task: async () => {
      const res = await familyMemberBills(m.user_id, { page_size: 50 });
      detailTarget.value = m;
      detailRows.value = res.items || [];
      detailTotal.value = res.total || 0;
    },
  });
}

function copyCode() {
  const code = info.value?.invite_code;
  if (!code) return;
  navigator.clipboard?.writeText(code).then(
    () => toast("邀请码已复制"),
    () => toast("复制失败，请手动复制", true),
  );
}

/* 分类条形图宽度：以最大分类值为 100% 基准，保底 4% 可见 */
function barWidth(value) {
  const max = summary.value?.categories?.[0]?.value || 1;
  return `${Math.max(4, Math.round((value / max) * 100))}%`;
}

/* ---- 家庭预算（T-7.3）：金额管理员设定，进度 = 全体成员支出之和 ---- */
const budget = ref(null);
const budgetForm = reactive({ category: "", amount: "" });

async function loadBudgets() {
  budget.value = await familyBudgetOverview(month.value);
}

function budgetPct(item) {
  if (!item.budget) return 0;
  return Math.min(100, Math.round((item.expense / item.budget) * 100));
}

async function doAddBudget() {
  const amount = Number(budgetForm.amount);
  if (!isFinite(amount) || amount <= 0) {
    toast("请输入有效的预算金额", true);
    return;
  }
  await runTask({
    key: "family:budget:add",
    title: "保存家庭预算",
    rethrow: false,
    successText: "家庭预算已保存",
    task: async () => {
      await upsertFamilyBudget({
        month: month.value,
        category: budgetForm.category,
        amount,
      });
      budgetForm.category = "";
      budgetForm.amount = "";
      await loadBudgets();
    },
  });
}

async function doDeleteBudget(item) {
  const ok = await confirm({
    title: "删除家庭预算",
    message: `确定删除${item.category ? `「${item.category}」` : "总预算"}的预算吗？`,
    danger: true,
    confirmText: "删除",
  });
  if (!ok) return;
  await runTask({
    key: "family:budget:del",
    title: "删除家庭预算",
    rethrow: false,
    successText: "已删除",
    task: async () => {
      await deleteFamilyBudget(item.id);
      await loadBudgets();
    },
  });
}

watch(month, () => {
  if (info.value) {
    loadSummary().catch(() => {});
    loadBudgets().catch(() => {});
  }
});

/* 面板在 App.vue 中始终挂载，激活时懒加载一次 */
let loadedOnce = false;
watch(
  () => store.tab,
  (tab) => {
    if (tab === "family" && !loadedOnce) {
      loadedOnce = true;
      load();
    }
  },
  { immediate: true },
);
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'family' }">
    <!-- 未加入家庭：创建 / 加入 双入口 -->
    <template v-if="loaded && !info">
      <div class="chart-box">
        <div class="section-head">
          <h3>创建家庭</h3>
          <span class="section-head__hint">
            创建后你成为家庭管理员，家人凭邀请码加入；各自记账，家庭页看总账
          </span>
        </div>
        <div class="add-row">
          <input
            v-model="createName"
            type="text"
            placeholder="家庭名称，如：我们家"
            maxlength="64"
            aria-label="家庭名称"
            @keydown.enter="doCreate"
          />
          <button class="btn primary" :disabled="creating" @click="doCreate">
            <AppIcon name="plus" :size="15" /> 创建家庭
          </button>
        </div>
      </div>
      <div class="chart-box">
        <div class="section-head">
          <h3>加入家庭</h3>
          <span class="section-head__hint">向家庭管理员要一个邀请码即可加入</span>
        </div>
        <div class="add-row">
          <input
            v-model="joinCode"
            class="code-input"
            type="text"
            placeholder="输入 8 位邀请码"
            maxlength="16"
            aria-label="家庭邀请码"
            @keydown.enter="doJoin"
          />
          <button class="btn primary" :disabled="joining" @click="doJoin">加入</button>
        </div>
      </div>
    </template>

    <!-- 已加入家庭 -->
    <template v-if="info">
      <div class="chart-box">
        <div class="section-head">
          <h3>
            {{ info.name }}
            <span class="role-badge" :class="{ admin: isAdmin }">
              {{ isAdmin ? "管理员" : "成员" }}
            </span>
          </h3>
          <span class="section-head__hint">成员各自记账互不干扰，家庭页只展示聚合值</span>
        </div>

        <!-- 邀请码仅管理员可见 -->
        <div v-if="isAdmin && info.invite_code" class="invite-row">
          <span class="invite-label">邀请码</span>
          <code class="invite-code">{{ info.invite_code }}</code>
          <button class="btn mini" @click="copyCode">复制</button>
          <button class="btn mini" title="旧邀请码将失效" @click="doRegenerate">
            <AppIcon name="refresh" :size="13" /> 重新生成
          </button>
        </div>

        <ul class="member-list">
          <li v-for="m in info.members" :key="m.user_id">
            <span class="member-name">
              {{ displayName(m) }}
              <span v-if="m.role === 'admin'" class="role-badge admin">管理员</span>
              <span v-if="m.user_id && info.members.length" class="member-uid">{{ m.user_id }}</span>
            </span>
            <span class="cat-actions">
              <button
                v-if="info.allow_detail_view && m.user_id"
                class="btn mini"
                @click="showDetail(m)"
              >
                {{ detailTarget?.user_id === m.user_id ? "收起流水" : "看流水" }}
              </button>
              <button
                v-if="isAdmin && m.role !== 'admin'"
                class="btn mini danger"
                @click="doRemove(m)"
              >
                移除
              </button>
            </span>
          </li>
        </ul>

        <div class="privacy-row">
          <label class="privacy-label">
            <input type="checkbox" :checked="info.allow_detail_view" @change="toggleDetail" />
            允许成员互看流水明细（默认关闭，家庭页始终只展示聚合值）
          </label>
        </div>

        <div class="danger-row">
          <button class="btn mini" @click="doLeave">退出家庭</button>
          <button v-if="isAdmin" class="btn mini danger" @click="doDisband">解散家庭</button>
        </div>
      </div>

      <!-- 月度聚合汇总 -->
      <div class="chart-box">
        <div class="section-head">
          <h3>家庭月度汇总</h3>
          <span class="section-head__hint">汇总 = 各成员之和，逐成员可核对</span>
        </div>
        <div class="filter-bar">
          <input v-model="month" type="month" title="汇总月份" aria-label="汇总月份" />
        </div>
        <div v-if="summary" class="summary-cards">
          <div class="sum-card">
            <span class="sum-label">收入</span>
            <span class="sum-value in">{{ fmtMoney(summary.totals.income) }}</span>
          </div>
          <div class="sum-card">
            <span class="sum-label">支出</span>
            <span class="sum-value out">{{ fmtMoney(summary.totals.expense) }}</span>
          </div>
          <div class="sum-card">
            <span class="sum-label">结余</span>
            <span class="sum-value" :class="summary.totals.net >= 0 ? 'in' : 'out'">
              {{ fmtMoney(summary.totals.net) }}
            </span>
          </div>
        </div>

        <table v-if="summary" class="member-table">
          <thead>
            <tr>
              <th>成员</th>
              <th class="num">收入</th>
              <th class="num">支出</th>
              <th class="num">结余</th>
              <th class="num">流水数</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in summary.members" :key="m.user_id">
              <td>{{ displayName(m) }}</td>
              <td class="num">{{ fmtMoney(m.income) }}</td>
              <td class="num">{{ fmtMoney(m.expense) }}</td>
              <td class="num" :class="m.net >= 0 ? 'in' : 'out'">{{ fmtMoney(m.net) }}</td>
              <td class="num">{{ m.bill_count }}</td>
            </tr>
          </tbody>
        </table>

        <div v-if="summary && summary.categories.length" class="cat-pie">
          <div class="section-head">
            <h4>全员支出分类</h4>
          </div>
          <div v-for="c in summary.categories.slice(0, 8)" :key="c.name" class="cat-row">
            <span class="cat-name">{{ c.name }}</span>
            <span class="cat-bar" :style="{ width: barWidth(c.value) }"></span>
            <span class="cat-value">{{ fmtMoney(c.value) }}</span>
          </div>
        </div>
      </div>

      <!-- 家庭预算（T-7.3）：管理员设定，进度 = 全体成员支出之和 -->
      <div class="chart-box">
        <div class="section-head">
          <h3>家庭预算</h3>
          <span class="section-head__hint">
            {{ isAdmin ? "家庭口径不按账本维度，覆盖全体成员当月支出" : "预算由家庭管理员设定，进度按全体成员支出汇总" }}
          </span>
        </div>

        <div v-if="isAdmin" class="filter-bar family-budget-add">
          <select v-model="budgetForm.category" title="预算分类，不选即家庭总预算" aria-label="预算分类">
            <option value="">总预算</option>
            <option v-for="c in categories" :key="c.id" :value="c.name">{{ c.name }}</option>
          </select>
          <input
            v-model="budgetForm.amount"
            type="number"
            step="0.01"
            min="1"
            placeholder="预算金额"
            aria-label="预算金额"
            @keydown.enter="doAddBudget"
          />
          <button
            class="btn mini primary"
            :disabled="isBusy('family:budget:add')"
            @click="doAddBudget"
          >
            <AppIcon name="plus" :size="14" /> {{ budget && budget.items.length ? "保存" : "添加" }}
          </button>
        </div>

        <div v-if="budget && budget.items.length" class="family-budget-list">
          <div v-for="i in budget.items" :key="i.id" class="fbudget-row">
            <span class="fbudget-name">{{ i.category || "总预算" }}</span>
            <span class="fbudget-bar">
              <span
                class="fbudget-fill"
                :class="{ over: i.remaining < 0 }"
                :style="{ width: budgetPct(i) + '%' }"
              ></span>
            </span>
            <span class="fbudget-num">
              {{ fmtMoney(i.expense) }} / {{ fmtMoney(i.budget) }}
              <em :class="i.remaining < 0 ? 'out' : 'in'">
                {{ i.remaining < 0 ? "超支 " + fmtMoney(-i.remaining) : "余 " + fmtMoney(i.remaining) }}
              </em>
            </span>
            <button
              v-if="isAdmin"
              class="btn mini danger"
              title="删除该预算"
              @click="doDeleteBudget(i)"
            >删除</button>
          </div>
        </div>
        <div v-else class="empty">
          {{ budget ? "本月还没有家庭预算" : "" }}
        </div>
      </div>

      <!-- 成员明细（仅 allow_detail_view 开启且用户点开时） -->
      <div v-if="detailTarget" class="chart-box">
        <div class="section-head">
          <h3>{{ displayName(detailTarget) }} 的流水</h3>
          <span class="section-head__hint">只读 · 最近 {{ detailTotal }} 条中的前 50 条</span>
        </div>
        <table class="member-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>商户</th>
              <th>分类</th>
              <th class="num">金额</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="b in detailRows" :key="b.id">
              <td>{{ b.tx_time }}</td>
              <td>{{ b.merchant || "-" }}</td>
              <td>{{ b.category }}</td>
              <td class="num" :class="b.tx_type === 'income' ? 'in' : 'out'">
                {{ b.tx_type === "income" ? "+" : "-" }}{{ fmtMoney(b.amount) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>

<style scoped>
.code-input {
  text-transform: uppercase;
  letter-spacing: 2px;
  font-family: var(--font-mono, monospace);
}
.role-badge {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 12px;
  background: var(--color-surface-2, #eee);
  color: var(--color-text-2, #666);
}
.role-badge.admin {
  background: var(--color-primary-soft, #e6f4ea);
  color: var(--color-primary, #1a7f37);
}
.invite-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0;
  flex-wrap: wrap;
}
.invite-label {
  color: var(--color-text-2, #666);
  font-size: 13px;
}
.invite-code {
  font-size: 16px;
  letter-spacing: 3px;
  padding: 2px 10px;
  border: 1px dashed var(--color-border, #ccc);
  border-radius: 6px;
}
.member-list {
  box-shadow: none;
  border: none;
  padding: 0;
  margin: 8px 0;
}
.member-name {
  display: flex;
  align-items: center;
  gap: 8px;
}
.member-uid {
  color: var(--color-text-3, #999);
  font-size: 12px;
}
.privacy-row {
  margin: 10px 0 4px;
}
.privacy-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--color-text-2, #555);
  cursor: pointer;
}
.danger-row {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
.summary-cards {
  display: flex;
  gap: 12px;
  margin: 12px 0;
  flex-wrap: wrap;
}
.sum-card {
  flex: 1;
  min-width: 120px;
  padding: 12px 16px;
  border-radius: 10px;
  background: var(--color-surface-2, #f6f7f8);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.sum-label {
  font-size: 12px;
  color: var(--color-text-2, #666);
}
.sum-value {
  font-size: 20px;
  font-weight: 600;
}
.in {
  color: var(--color-income, #1a7f37);
}
.out {
  color: var(--color-expense, #c0392b);
}
.member-table {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0;
  font-size: 13px;
}
.member-table th,
.member-table td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--color-border, #eee);
  text-align: left;
}
.member-table th.num,
.member-table td.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.cat-pie {
  margin-top: 8px;
}
.cat-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
}
.cat-row .cat-name {
  width: 90px;
  flex: none;
}
.cat-bar {
  height: 8px;
  border-radius: 4px;
  background: var(--color-primary, #1a7f37);
  opacity: 0.75;
}
.cat-value {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}
  .family-budget-add {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 8px;
  }
  .family-budget-add select,
  .family-budget-add input {
    flex: 0 1 auto;
  }
  .family-budget-add input {
    width: 9em;
  }
  .family-budget-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .fbudget-row {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .fbudget-name {
    min-width: 4.5em;
    font-weight: 600;
  }
  .fbudget-bar {
    flex: 1;
    height: 8px;
    border-radius: 4px;
    background: rgba(0, 0, 0, 0.08);
    overflow: hidden;
    display: inline-block;
  }
  .fbudget-fill {
    display: block;
    height: 100%;
    border-radius: 4px;
    background: var(--accent, #e8833a);
  }
  .fbudget-fill.over {
    background: #d64545;
  }
  .fbudget-num {
    min-width: 0;
    font-variant-numeric: tabular-nums;
  }
  .fbudget-num em {
    font-style: normal;
    margin-left: 6px;
  }
  .fbudget-num em.in {
    color: #2e8b57;
  }
  .fbudget-num em.out {
    color: #d64545;
  }
</style>