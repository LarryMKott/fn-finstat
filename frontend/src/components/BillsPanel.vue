<script setup>
/* 流水管理：本页是应用的核心工作台，改造重点是「可读性」与「操作效率」。
 *   1. 金额等宽右对齐 + 收支语义色（收红支绿）+ 正负号，扫一眼就能读懂资金的进出
 *   2. 日期与时间分行，同日省略年份，降低视觉噪音
 *   3. 行内操作收进菜单，表格宽度可控、焦点集中在数据本身
 *   4. 筛选条件分级：常用的账户/类型/分类常驻，低频条件收进「更多筛选」
 *   5. 移动端整体切换为卡片列表，不再横滑宽表格
 *   6. 批量操作改为吸顶悬浮条，选中后始终可见且不挤压表格 */
import { computed, reactive, ref, watch } from "vue";
import { api, apiUrl } from "../api";
import {
  fmtAccount,
  fmtSignedMoney,
  fmtType,
  accountDotClass,
  amountClass,
  splitDateTime,
  splitTags,
  tagClass,
} from "../format";
import { categories, store } from "../store";
import { toast } from "../toast";
import AppIcon from "./AppIcon.vue";
import BillModal from "./BillModal.vue";
import RowActionsMenu from "./RowActionsMenu.vue";

const PAGE_SIZE = 20;

/* 可排序列（与后端白名单一致） */
const columns = [
  { key: "tx_time", label: "交易时间" },
  { key: "account", label: "账户" },
  { key: "merchant", label: "商户" },
  { key: "amount", label: "金额", num: true },
  { key: "category", label: "分类" },
  { key: "remark", label: "备注" },
];

const ACCOUNTS = [
  { value: "wechat", label: "微信" },
  { value: "alipay", label: "支付宝" },
  { value: "jd", label: "京东" },
  { value: "unionpay", label: "云闪付" },
];
const TX_TYPES = [
  { value: "expense", label: "支出" },
  { value: "income", label: "收入" },
  { value: "transfer", label: "转账" },
];

const filters = reactive({
  start: "",
  end: "",
  account: "",
  tx_type: "",
  category: "",
  tag: "",
  reimbursed: "",
});
const bills = ref([]);
const total = ref(0);
const page = ref(1);
const sortBy = ref("tx_time");
const sortOrder = ref("desc");
const modal = ref({ show: false, bill: null });

/* 回收站模式：列表切换为已删除流水 */
const recycleMode = ref(false);

/* 高级筛选展开态：有值时默认展开，避免用户看不出筛选正在生效 */
const advancedOpen = ref(false);

/* 多选与批量操作 */
const selected = ref(new Set());
const batchBar = reactive({ action: "", category: "", tags: "", reimbursed: true });
const batchBusy = ref(false);

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)));
const allChecked = computed(
  () => bills.value.length > 0 && bills.value.every((b) => selected.value.has(b.id)),
);

/* 生效中的高级筛选数量，用于折叠态给出提示 */
const advancedCount = computed(
  () => [filters.tx_type, filters.category, filters.tag, filters.reimbursed].filter(Boolean).length,
);

const rowActions = computed(() =>
  recycleMode.value
    ? [
        { key: "restore", label: "还原", icon: "restore" },
        { key: "purge", label: "彻底删除", icon: "trash", danger: true },
      ]
    : [
        { key: "edit", label: "编辑", icon: "edit" },
        { key: "delete", label: "移入回收站", icon: "trash", danger: true },
      ],
);

function filterParams(extra = {}) {
  const params = new URLSearchParams({ ...extra });
  for (const [k, v] of Object.entries(filters)) {
    if (v) params.set(k, v);
  }
  return params;
}

async function load() {
  try {
    const path = recycleMode.value ? "/api/bill/recycle" : "/api/bill/list";
    /* 回收站接口只收分页参数：排序/筛选 UI 在回收站模式下不可用，发过去也是假象 */
    const params = recycleMode.value
      ? new URLSearchParams({ page: page.value, page_size: PAGE_SIZE })
      : filterParams({ page: page.value, page_size: PAGE_SIZE, sort_by: sortBy.value, order: sortOrder.value });
    const data = await api(`${path}?` + params.toString());
    bills.value = data.items;
    total.value = data.total;
    selected.value = new Set();
  } catch (err) {
    toast("流水加载失败：" + err.message, true);
  }
}

/* 批量移除（删/还原/清空）后若当前页已超界，先回退页码再加载，避免停在空页 */
function clampPageAfterRemoval(removed) {
  const remaining = Math.max(0, total.value - removed);
  const lastPage = Math.max(1, Math.ceil(remaining / PAGE_SIZE));
  if (page.value > lastPage) page.value = lastPage;
}

function toggleSort(key) {
  if (sortBy.value === key) {
    sortOrder.value = sortOrder.value === "asc" ? "desc" : "asc";
  } else {
    sortBy.value = key;
    sortOrder.value = key === "tx_time" || key === "amount" ? "desc" : "asc";
  }
  page.value = 1;
  load();
}

function sortIcon(key) {
  if (sortBy.value !== key) return "⇅";
  return sortOrder.value === "asc" ? "▲" : "▼";
}

function search() {
  page.value = 1;
  load();
}

function resetFilters() {
  Object.assign(filters, {
    start: "",
    end: "",
    account: "",
    tx_type: "",
    category: "",
    tag: "",
    reimbursed: "",
  });
  sortBy.value = "tx_time";
  sortOrder.value = "desc";
  page.value = 1;
  load();
}

function prevPage() {
  if (page.value > 1) {
    page.value--;
    load();
  }
}

function nextPage() {
  if (page.value < totalPages.value) {
    page.value++;
    load();
  }
}

function openCreate() {
  modal.value = { show: true, bill: null };
}

function openEdit(bill) {
  modal.value = { show: true, bill };
}

async function removeBill(bill) {
  if (!confirm(`把「${bill.merchant || bill.tx_time}」移入回收站吗？\n（可在回收站还原）`)) return;
  try {
    await api("/api/bill/" + bill.id, { method: "DELETE" });
    toast("已移入回收站");
    if (bills.value.length === 1 && page.value > 1) page.value--;
    load();
  } catch (err) {
    toast(err.message, true);
  }
}

/* ---- 多选 ---- */
function toggleSelect(bill) {
  if (selected.value.has(bill.id)) selected.value.delete(bill.id);
  else selected.value.add(bill.id);
  selected.value = new Set(selected.value); // 触发响应式
}

function toggleSelectAll() {
  if (allChecked.value) selected.value.clear();
  else bills.value.forEach((b) => selected.value.add(b.id));
  selected.value = new Set(selected.value);
}

const batchOpen = computed(() => selected.value.size > 0 && !recycleMode.value);
const recycleOpen = computed(() => selected.value.size > 0 && recycleMode.value);

function openBatch(action) {
  batchBar.action = action;
  batchBar.category = "";
  batchBar.tags = "";
  batchBar.reimbursed = true;
}

async function runBatch() {
  const ids = [...selected.value];
  const action = batchBar.action;
  if (!ids.length) return;
  if (action === "set_category" && !batchBar.category) {
    toast("请选择目标分类", true);
    return;
  }
  if (action === "set_tags" && !batchBar.tags.trim() && !confirm("标签为空将清空所选流水的全部标签，继续吗？")) return;
  batchBusy.value = true;
  try {
    const res = await api("/api/bill/batch", {
      method: "POST",
      body: JSON.stringify({
        ids,
        action,
        category: batchBar.category,
        tags: batchBar.tags,
        reimbursed: batchBar.reimbursed,
      }),
    });
    toast(`批量操作完成：更新 ${res.updated} 条`);
    batchBar.action = "";
    if (action === "delete" || action === "purge") clampPageAfterRemoval(res.updated);
    load();
  } catch (err) {
    toast(err.message, true);
  } finally {
    batchBusy.value = false;
  }
}

async function removeBatch() {
  if (!confirm(`把选中的 ${selected.value.size} 条流水移入回收站吗？`)) return;
  try {
    const res = await api("/api/bill/batch", {
      method: "POST",
      body: JSON.stringify({ ids: [...selected.value], action: "delete" }),
    });
    toast(`已移入回收站 ${res.updated} 条`);
    clampPageAfterRemoval(res.updated);
    load();
  } catch (err) {
    toast(err.message, true);
  }
}

/* ---- 回收站操作 ---- */
async function restoreSelected() {
  const ids = [...selected.value];
  if (!ids.length) return;
  try {
    const res = await api("/api/bill/recycle/restore", {
      method: "POST",
      body: JSON.stringify({ ids }),
    });
    toast(`已还原 ${res.updated} 条`);
    clampPageAfterRemoval(res.updated);
    load();
  } catch (err) {
    toast(err.message, true);
  }
}

async function restoreOne(bill) {
  /* 单行还原独立请求，不借用多选集合——避免静默覆盖用户已勾选的条目 */
  try {
    const res = await api("/api/bill/recycle/restore", {
      method: "POST",
      body: JSON.stringify({ ids: [bill.id] }),
    });
    toast(`已还原 ${res.updated} 条`);
    clampPageAfterRemoval(res.updated);
    load();
  } catch (err) {
    toast(err.message, true);
  }
}

async function purgeSelected() {
  const ids = [...selected.value];
  if (!ids.length) return;
  if (!confirm(`彻底删除选中的 ${ids.length} 条流水？\n彻底删除后不可恢复！`)) return;
  try {
    const res = await api("/api/bill/recycle", {
      method: "DELETE",
      body: JSON.stringify({ ids }),
    });
    toast(`已彻底删除 ${res.updated} 条`);
    clampPageAfterRemoval(res.updated);
    load();
  } catch (err) {
    toast(err.message, true);
  }
}

async function purgeOne(bill) {
  if (!confirm("彻底删除该流水？\n彻底删除后不可恢复！")) return;
  try {
    const res = await api("/api/bill/recycle", {
      method: "DELETE",
      body: JSON.stringify({ ids: [bill.id] }),
    });
    toast(`已彻底删除 ${res.updated} 条`);
    clampPageAfterRemoval(res.updated);
    load();
  } catch (err) {
    toast(err.message, true);
  }
}

async function emptyRecycle() {
  if (!confirm("清空回收站？\n其中全部流水将被彻底删除，不可恢复！")) return;
  try {
    const res = await api("/api/bill/recycle/empty", { method: "POST" });
    toast(`已清空 ${res.updated} 条`);
    page.value = 1;
    load();
  } catch (err) {
    toast(err.message, true);
  }
}

function switchRecycle() {
  recycleMode.value = !recycleMode.value;
  page.value = 1;
  sortBy.value = "tx_time";
  sortOrder.value = "desc";
  load();
}

/* ---- 行操作分发 ---- */
function onRowAction(bill, key) {
  if (key === "edit") openEdit(bill);
  else if (key === "delete") removeBill(bill);
  else if (key === "restore") restoreOne(bill);
  else if (key === "purge") purgeOne(bill);
}

/* ---- 导出 ---- */
function exportBills(format) {
  const qs = filterParams({ format }).toString();
  window.open(apiUrl(`/api/bill/export?${qs}`), "_blank");
}

const aiBusy = ref(false);

async function aiClassify() {
  if (
    !confirm(
      "把当前账号中分类为「其他」的流水交给 DeepSeek 重新归类吗？\n\n" +
        "单次最多处理 1000 条（未处理完可再次点击），调用会产生少量 API 费用；" +
        "需先在「设置」页配置 DeepSeek API Key。",
    )
  )
    return;
  aiBusy.value = true;
  try {
    const res = await api("/api/ai/classify", {
      method: "POST",
      body: JSON.stringify({ scope: "unmatched" }),
    });
    toast(
      res.processed > 0
        ? `AI 智能分类完成：检查 ${res.processed} 条，更新 ${res.changed} 条分类`
        : res.message || "没有需要归类的流水",
    );
    load();
  } catch (err) {
    toast(err.message, true);
  } finally {
    aiBusy.value = false;
  }
}

watch(
  () => store.tab === "bills",
  (active) => {
    if (active) load();
  },
  { immediate: true },
);
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'bills' }">
    <!-- 常驻筛选：只放高频条件，保持一行清爽；回收站不支持筛选/排序/导出，仅保留入口切换 -->
    <div class="filter-bar">
      <template v-if="!recycleMode">
        <input v-model="filters.start" type="date" title="起始日期" aria-label="起始日期" />
        <span class="sep">至</span>
        <input v-model="filters.end" type="date" title="结束日期" aria-label="结束日期" />
        <select v-model="filters.account" title="账户" aria-label="账户">
          <option value="">全部账户</option>
          <option v-for="a in ACCOUNTS" :key="a.value" :value="a.value">{{ a.label }}</option>
        </select>

        <button
          class="filter-toggle"
          :class="{ 'is-open': advancedOpen }"
          type="button"
          :aria-expanded="advancedOpen"
          @click="advancedOpen = !advancedOpen"
        >
          <AppIcon name="filter" :size="15" />
          更多筛选
          <span v-if="advancedCount" class="filter-count">{{ advancedCount }}</span>
          <AppIcon class="filter-toggle__chevron" name="chevronDown" :size="14" />
        </button>

        <button class="btn primary" @click="search">查询</button>
        <button class="btn ghost" @click="resetFilters">重置</button>
      </template>
      <span v-else class="hint">回收站内可整页还原或彻底删除；返回流水后可继续筛选排序</span>

      <div class="filter-bar__tail right">
        <template v-if="!recycleMode">
          <button class="btn ghost" title="按当前筛选条件导出 xlsx" @click="exportBills('xlsx')">
            <AppIcon name="download" :size="15" /> Excel
          </button>
          <button class="btn ghost" title="按当前筛选条件导出 CSV" @click="exportBills('csv')">
            <AppIcon name="download" :size="15" /> CSV
          </button>
        </template>
        <button class="btn ghost" :class="{ 'btn-warn': recycleMode }" @click="switchRecycle">
          <AppIcon name="recycle" :size="15" />
          {{ recycleMode ? "返回流水" : "回收站" }}
        </button>
        <button v-if="!recycleMode" class="btn ghost" @click="openCreate">
          <AppIcon name="plus" :size="15" /> 新增
        </button>
        <button v-if="recycleMode" class="btn ghost danger" @click="emptyRecycle">清空回收站</button>
      </div>
    </div>

    <!-- 高级筛选：低频条件，展开时下拉出，不展开不占空间 -->
    <div class="filter-advanced" :class="{ 'is-open': advancedOpen }">
      <div class="filter-advanced__inner">
        <select v-model="filters.tx_type" title="收支类型" aria-label="收支类型">
          <option value="">全部类型</option>
          <option v-for="t in TX_TYPES" :key="t.value" :value="t.value">{{ t.label }}</option>
        </select>
        <select v-model="filters.category" title="消费分类" aria-label="消费分类">
          <option value="">全部分类</option>
          <option v-for="c in categories" :key="c.id" :value="c.name">{{ c.name }}</option>
        </select>
        <input v-model="filters.tag" type="text" placeholder="标签（精确匹配）" class="filter-tag" aria-label="标签" />
        <select v-model="filters.reimbursed" title="报销筛选" class="filter-reimburse" aria-label="报销筛选">
          <option value="">报销全部</option>
          <option value="true">已报销</option>
          <option value="false">未报销</option>
        </select>
        <button class="btn primary" @click="search">应用筛选</button>
        <span class="hint">标签为精确匹配；报销筛选配合标签可快速找出待报销支出</span>
      </div>
    </div>

    <!-- AI 归类：低频但重要的批量动作，单独一行不与筛选混排 -->
    <div v-if="!recycleMode" class="tool-strip">
      <span class="hint">
        分类为「其他」的流水可交给 DeepSeek 语义归类，单次最多 1000 条
      </span>
      <button class="btn" :disabled="aiBusy" @click="aiClassify">
        <AppIcon name="sparkles" :size="15" />
        {{ aiBusy ? "AI 分类中…" : "AI 智能分类" }}
      </button>
    </div>

    <!-- 批量操作条：吸顶悬浮，选中后始终可见 -->
    <div v-if="batchOpen" class="batch-bar">
      <span>已选 <b>{{ selected.size }}</b> 条</span>
      <button class="btn mini" :class="{ primary: batchBar.action === 'set_category' }" @click="openBatch('set_category')">改分类</button>
      <button class="btn mini" :class="{ primary: batchBar.action === 'set_tags' }" @click="openBatch('set_tags')">打标签</button>
      <button class="btn mini" :class="{ primary: batchBar.action === 'set_reimbursed' }" @click="openBatch('set_reimbursed')">报销标记</button>
      <button class="btn mini danger" @click="removeBatch">移入回收站</button>
      <button class="btn mini ghost" @click="selected = new Set()">取消选择</button>
      <span class="batch-bar__spacer"></span>
      <template v-if="batchBar.action">
        <select v-if="batchBar.action === 'set_category'" v-model="batchBar.category">
          <option value="">选择分类…</option>
          <option v-for="c in categories" :key="c.id" :value="c.name">{{ c.name }}</option>
        </select>
        <input v-if="batchBar.action === 'set_tags'" v-model="batchBar.tags" type="text" placeholder="逗号分隔（覆盖原标签）" />
        <label v-if="batchBar.action === 'set_reimbursed'" class="batch-radio">
          <input v-model="batchBar.reimbursed" type="radio" :value="true" /> 标记报销
          <input v-model="batchBar.reimbursed" type="radio" :value="false" /> 取消报销
        </label>
        <button class="btn mini primary" :disabled="batchBusy" @click="runBatch">
          {{ batchBusy ? "执行中…" : "应用" }}
        </button>
      </template>
    </div>

    <div v-if="recycleOpen" class="batch-bar">
      <span>回收站已选 <b>{{ selected.size }}</b> 条</span>
      <button class="btn mini primary" @click="restoreSelected">还原所选</button>
      <button class="btn mini danger" @click="purgeSelected">彻底删除</button>
      <button class="btn mini ghost" @click="selected = new Set()">取消选择</button>
    </div>

    <!-- 空状态 -->
    <div v-if="!bills.length" class="table-wrap">
      <p class="empty">
        {{ recycleMode ? "回收站是空的" : "暂无数据，去「账单导入」上传或点右上角「新增」手动记一笔" }}
      </p>
    </div>

    <!-- 桌面端：数据表（表格滚动时表头吸顶） -->
    <div v-else class="table-wrap bill-table-desktop">
      <div class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th class="col-check"><input type="checkbox" :checked="allChecked" aria-label="全选本页" @change="toggleSelectAll" /></th>
              <th v-for="c in columns" :key="c.key" :class="{ num: c.num, sortable: !recycleMode }"
                  :title="recycleMode ? null : `按${c.label}排序`" @click="!recycleMode && toggleSort(c.key)">
                {{ c.label }}<span class="sort" :class="{ active: !recycleMode && sortBy === c.key }">{{ sortIcon(c.key) }}</span>
              </th>
              <th>标签</th>
              <th class="col-ops"></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="b in bills" :key="b.id" :class="{ checked: selected.has(b.id) }">
              <td class="col-check"><input type="checkbox" :checked="selected.has(b.id)" :aria-label="`选择 ${b.merchant || b.tx_time}`" @change="toggleSelect(b)" /></td>
              <td class="cell-time">
                {{ splitDateTime(b.tx_time).date }}
                <span class="cell-sub">{{ splitDateTime(b.tx_time).time }}</span>
              </td>
              <td>
                <span class="acct">
                  <span class="acct__dot" :class="accountDotClass(b.account)"></span>
                  {{ fmtAccount(b.account) }}
                </span>
              </td>
              <td class="cell-primary" :title="b.merchant">{{ b.merchant || "—" }}</td>
              <td class="money-cell" :class="amountClass(b.tx_type)">
                {{ fmtSignedMoney(b.amount, b.tx_type) }}
                <small>{{ fmtType(b.tx_type) }}</small>
              </td>
              <td><span class="tag" :class="tagClass(b.tx_type)">{{ b.category }}</span></td>
              <td class="cell-secondary" :title="b.remark">{{ (b.remark || "").slice(0, 18) || "—" }}</td>
              <td>
                <span v-for="t in splitTags(b.tags)" :key="t" class="chip">{{ t }}</span>
                <span v-if="b.reimbursed" class="chip chip-reimburse" title="已标记报销">报销</span>
                <span v-if="!b.tags && !b.reimbursed" class="cell-secondary">—</span>
              </td>
              <td class="col-ops">
                <RowActionsMenu :items="rowActions" @select="onRowAction(b, $event)" />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 移动端：卡片列表，金额右对齐形成可比对的视觉列 -->
    <div v-if="bills.length" class="bill-cards bill-cards-mobile">
      <div v-for="b in bills" :key="b.id" class="bill-card" :class="{ checked: selected.has(b.id) }">
        <input
          type="checkbox"
          class="bill-card__check"
          :checked="selected.has(b.id)"
          :aria-label="`选择 ${b.merchant || b.tx_time}`"
          @change="toggleSelect(b)"
        />
        <div class="bill-card__body">
          <div class="bill-card__top">
            <span class="bill-card__merchant">{{ b.merchant || "—" }}</span>
            <span class="bill-card__amount" :class="amountClass(b.tx_type)">
              {{ fmtSignedMoney(b.amount, b.tx_type) }}
            </span>
          </div>
          <div class="bill-card__meta">
            <span class="acct">
              <span class="acct__dot" :class="accountDotClass(b.account)"></span>
              {{ fmtAccount(b.account) }}
            </span>
            <span class="sep-dot"></span>
            <span>{{ fmtType(b.tx_type) }}</span>
            <span class="sep-dot"></span>
            <span>{{ splitDateTime(b.tx_time).date }} {{ splitDateTime(b.tx_time).time }}</span>
          </div>
          <div class="bill-card__meta">
            <span class="tag" :class="tagClass(b.tx_type)">{{ b.category }}</span>
            <span v-if="b.remark" class="bill-card__remark">{{ b.remark }}</span>
          </div>
          <div v-if="b.tags || b.reimbursed" class="bill-card__tags">
            <span v-for="t in splitTags(b.tags)" :key="t" class="chip">{{ t }}</span>
            <span v-if="b.reimbursed" class="chip chip-reimburse">报销</span>
          </div>
        </div>
        <RowActionsMenu :items="rowActions" @select="onRowAction(b, $event)" />
      </div>
    </div>

    <div class="pager">
      <button class="btn" :disabled="page <= 1" @click="prevPage">上一页</button>
      <span class="pager__info">第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 条</span>
      <button class="btn" :disabled="page >= totalPages" @click="nextPage">下一页</button>
    </div>

    <BillModal
      :show="modal.show"
      :bill="modal.bill"
      @close="modal.show = false"
      @saved="load"
    />
  </section>
</template>

<style scoped>
/* 表格列宽策略：时间/账户/分类/标签紧凑，商户与备注弹性，金额固定右对齐 */
.col-check {
  width: 36px;
  text-align: center;
}
.col-ops {
  width: 46px;
  text-align: right;
}

.cell-sub {
  display: block;
  font-size: var(--text-2xs);
  color: var(--color-text-tertiary);
}

/* 筛选栏尾部按钮组：移动端自动换行 */
.filter-bar__tail {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex-wrap: wrap;
}

/* 工具条：AI 归类等低频批量动作 */
.tool-strip {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
  margin-bottom: var(--space-3);
}
.tool-strip .hint {
  flex: 1;
  min-width: 200px;
}

.batch-bar__spacer {
  flex: 1;
  min-width: 0;
}

/* 桌面/移动形态互斥 */
.bill-cards-mobile {
  display: none;
}
.bill-card__check {
  margin-top: var(--space-1);
  flex-shrink: 0;
}
.bill-card__remark {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 60%;
}

@media (max-width: 860px) {
  .bill-table-desktop {
    display: none;
  }
  .bill-cards-mobile {
    display: flex;
  }
  .filter-bar__tail {
    flex: 1 1 100%;
  }
  .filter-bar__tail .btn {
    flex: 1 1 30%;
  }
  .tool-strip .btn {
    width: 100%;
    min-height: 42px;
  }
}
</style>
