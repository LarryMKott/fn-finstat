<script setup>
/* 流水管理：本页是应用的核心工作台，改造重点是「可读性」与「操作效率」。
 *   1. 金额等宽右对齐 + 收支语义色（收红支绿）+ 正负号，扫一眼就能读懂资金的进出
 *   2. 日期与时间分行，同日省略年份，降低视觉噪音
 *   3. 行内操作收进菜单，表格宽度可控、焦点集中在数据本身
 *   4. 筛选条件分级：常用的账户/类型/分类常驻，低频条件收进「更多筛选」
 *   5. 移动端整体切换为卡片列表，不再横滑宽表格
 *   6. 批量操作改为吸顶悬浮条，选中后始终可见且不挤压表格 */
import { computed, reactive, ref, watch } from "vue";
import {
  batchBills,
  deleteBill,
  emptyRecycle,
  exportBillsUrl,
  listBills,
  listRecycle,
  purgeBills,
  restoreBills,
} from "../api/bill";
import { classifyBills } from "../api/ai";
import {
  fmtAccount,
  fmtSignedMoney,
  fmtType,
  accountDotClass,
  amountClass,
  splitDateTime,
  splitTags,
  tagClass,
} from "../utils/format";
import { ACCOUNTS, TX_TYPES } from "../utils/constants";
import { confirm } from "../composables/useConfirm";
import { isBusy, runTask } from "../composables/useLoading";
import { categories, billsFilterHandoff, store } from "../store";
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

const filters = reactive({
  start: "",
  end: "",
  account: "",
  tx_type: "",
  category: "",
  tag: "",
  reimbursed: "",
  /* 问账「存为筛选」带入的多值口径（T-6.2）：无对应 UI 控件，
   * 由下方口径 chip 展示与清除 */
  categories: [],
  merchants: [],
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

/* 防重复提交统一交给 loading 层的 key 锁，组件内不再各自维护 busy 标志 */
const batchBusy = computed(() => isBusy("bills:batch"));
const aiBusy = computed(() => isBusy("bills:ai-classify"));

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
    if (Array.isArray(v)) {
      for (const item of v) if (item) params.append(k, item);
    } else if (v) {
      params.set(k, v);
    }
  }
  return params;
}

/* 查询类：允许新请求接管（mode=latest），快速翻页时旧响应作废，不会覆盖新结果。
 * 注意 latest 只作废浮层状态、不会取消已在途的 Promise，故再加一层序号校验兜底。 */
let loadSeq = 0;

async function load() {
  await runTask({
    key: "bills:load",
    title: recycleMode.value ? "加载回收站" : "加载流水",
    detail: `正在读取第 ${page.value} 页…`,
    mode: "latest",
    successText: (data) => `共 ${data ? data.total : 0} 条`,
    rethrow: false,
    task: async () => {
      const seq = ++loadSeq;
      /* 回收站接口只收分页参数：排序/筛选 UI 在回收站模式下不可用，发过去也是假象 */
      const request = recycleMode.value
        ? listRecycle(page.value, PAGE_SIZE)
        : listBills({
            page: page.value,
            page_size: PAGE_SIZE,
            sort_by: sortBy.value,
            order: sortOrder.value,
            ...filters,
          });
      try {
        const data = await request;
        /* 已有更新的查询发出：本次结果作废，不覆盖新页数据 */
        if (seq !== loadSeq) return null;
        bills.value = data.items;
        total.value = data.total;
        selected.value = new Set();
        return data;
      } catch (err) {
        throw new Error("流水加载失败：" + err.message);
      }
    },
  });
}

/* 批量移除（删/还原/清空）后若当前页已超界，先回退页码再加载，避免停在空页 */
function clampPageAfterRemoval(removed) {
  const remaining = Math.max(0, total.value - removed);
  const lastPage = Math.max(1, Math.ceil(remaining / PAGE_SIZE));
  if (page.value > lastPage) page.value = lastPage;
}

/* 单条/批量/回收站共用的操作执行器：确认 → 请求 → 提示 → 页码回退 → 刷新
 *   key / title  交给 loading 层的任务标识与名称（key 同时是防重入粒度）
 *   confirmOpts  确认弹窗参数（title/message/danger/confirmText），缺省不弹确认
 *   request      async (update) => {updated} 实际请求，可用 update() 汇报进度文案
 *   message      (updated) => string   成功提示文案
 *   resetPage    操作后回到第 1 页（清空回收站） */
async function runBillOperation({
  key,
  title,
  detail = "正在提交…",
  confirm: confirmOpts = null,
  request,
  message,
  resetPage = false,
}) {
  if (confirmOpts && !(await confirm(confirmOpts))) return;
  let done = false;
  const res = await runTask({
    key,
    title,
    detail,
    rethrow: false,
    successText: (result) => message(result ? result.updated : null),
    task: async (update) => {
      const result = await request(update);
      done = true;
      return result;
    },
  });
  /* 失败或已被防重入拦截：进度浮层已给出原因，这里不再继续后续步骤。
   * 只看 done 不看 res —— 删除类接口返回 204，runTask 解析结果是 null，
   * 若把「返回值真假」当成成功判据，删除后会静默跳过页码回退与刷新。 */
  if (!done) return;
  const updated = res && res.updated ? res.updated : 0;
  if (resetPage) page.value = 1;
  else clampPageAfterRemoval(updated);
  load();
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
  if (sortBy.value !== key) return "sort";
  return sortOrder.value === "asc" ? "sortAsc" : "sortDesc";
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
    categories: [],
    merchants: [],
  });
  sortBy.value = "tx_time";
  sortOrder.value = "desc";
  page.value = 1;
  load();
}

/* 问账口径 chip 清除：只清多值口径，常规筛选保留 */
function clearHandoffScope() {
  filters.categories = [];
  filters.merchants = [];
  page.value = 1;
  load();
}

const handoffScopeLabel = computed(() => {
  const parts = [];
  if (filters.categories.length) parts.push(`分类 ${filters.categories.join("、")}`);
  if (filters.merchants.length) parts.push(`商户含 ${filters.merchants.join("、")}`);
  return parts.join(" · ");
});

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

const removeBill = (bill) =>
  runBillOperation({
    key: "bills:delete",
    title: "移入回收站",
    detail: `正在处理「${bill.merchant || bill.tx_time}」…`,
    confirm: {
      title: "移入回收站",
      message: `把「${bill.merchant || bill.tx_time}」移入回收站吗？\n（可在回收站还原）`,
      confirmText: "移入",
    },
    request: () => deleteBill(bill.id),
    message: () => "已移入回收站",
  });

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
  if (
    action === "set_tags" &&
    !batchBar.tags.trim() &&
    !(await confirm({ title: "清空标签", message: "标签为空将清空所选流水的全部标签，继续吗？" }))
  )
    return;
  const res = await runTask({
    key: "bills:batch",
    title: "批量操作",
    detail: `正在更新 ${ids.length} 条流水…`,
    rethrow: false,
    successText: (r) => `已更新 ${r ? r.updated : 0} 条`,
    task: () =>
      batchBills({
        ids,
        action,
        category: batchBar.category,
        tags: batchBar.tags,
        reimbursed: batchBar.reimbursed,
      }),
  });
  if (!res) return;
  batchBar.action = "";
  if (action === "delete" || action === "purge") clampPageAfterRemoval(res.updated);
  load();
}

const removeBatch = () =>
  runBillOperation({
    key: "bills:batch-delete",
    title: "批量移入回收站",
    detail: `正在移入 ${selected.value.size} 条流水…`,
    confirm: {
      title: "批量移入回收站",
      message: `把选中的 ${selected.value.size} 条流水移入回收站吗？`,
      confirmText: "移入",
    },
    request: () => batchBills({ ids: [...selected.value], action: "delete" }),
    message: (updated) => `已移入回收站 ${updated} 条`,
  });

/* ---- 回收站操作 ---- */
/* key 可按调用方细分：批量操作共用一个锁，单行操作按 id 各自加锁，
   否则连续点两行会被「正在执行，请稍候」拦掉第二条（用户会以为点了没反应） */
const restoreByIds = (ids, key = "bills:restore") =>
  runBillOperation({
    key,
    title: "还原流水",
    detail: `正在还原 ${ids.length} 条…`,
    request: () => restoreBills(ids),
    message: (updated) => `已还原 ${updated} 条`,
  });

const restoreSelected = () => restoreByIds([...selected.value]);

/* 单行还原独立请求，不借用多选集合——避免静默覆盖用户已勾选的条目 */
const restoreOne = (bill) => restoreByIds([bill.id], `bills:restore:${bill.id}`);

const purgeByIds = (ids, key = "bills:purge") =>
  runBillOperation({
    key,
    title: "彻底删除",
    detail: `正在删除 ${ids.length} 条流水…`,
    confirm: {
      title: "彻底删除",
      message: `彻底删除选中的 ${ids.length} 条流水？\n彻底删除后不可恢复！`,
      danger: true,
      confirmText: "彻底删除",
    },
    request: () => purgeBills(ids),
    message: (updated) => `已彻底删除 ${updated} 条`,
  });

const purgeSelected = () => purgeByIds([...selected.value]);

const purgeOne = (bill) =>
  runBillOperation({
    key: `bills:purge:${bill.id}`,
    title: "彻底删除",
    detail: `正在删除「${bill.merchant || bill.tx_time}」…`,
    confirm: {
      title: "彻底删除",
      message: "彻底删除该流水？\n彻底删除后不可恢复！",
      danger: true,
      confirmText: "彻底删除",
    },
    request: () => purgeBills([bill.id]),
    message: (updated) => `已彻底删除 ${updated} 条`,
  });

const emptyRecycleNow = () =>
  runBillOperation({
    key: "bills:empty-recycle",
    title: "清空回收站",
    detail: "正在清空回收站…",
    confirm: {
      title: "清空回收站",
      message: "清空回收站？\n其中全部流水将被彻底删除，不可恢复！",
      danger: true,
      confirmText: "清空",
    },
    request: () => emptyRecycle(),
    message: (updated) => `已清空 ${updated} 条`,
    resetPage: true,
  });

function switchRecycle() {
  recycleMode.value = !recycleMode.value;
  /* 选中集属于切换前的列表：不同步清空的话，加载失败/进行中时悬浮条
   * 会把旧列表的选中 id 当成回收站条目，对屏幕上不存在的行执行批量操作 */
  selected.value = new Set();
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
  window.open(exportBillsUrl(Object.fromEntries(filterParams({ format }))), "_blank");
}

async function aiClassify() {
  if (
    !(await confirm({
      title: "AI 智能分类",
      message:
        "把当前账号中分类为「其他」的流水交给 DeepSeek 重新归类吗？\n\n" +
        "单次最多处理 1000 条（未处理完可再次点击），调用会产生少量 API 费用；" +
        "需先在「设置」页配置 DeepSeek API Key。",
      confirmText: "开始归类",
    }))
  )
    return;
  const res = await runTask({
    key: "bills:ai-classify",
    title: "AI 智能分类",
    detail: "正在调用 DeepSeek 归类「其他」流水…",
    rethrow: false,
    successText: (res) => {
      if (res && res.processed > 0) {
        return `检查 ${res.processed} 条，更新 ${res.changed} 条分类`;
      }
      return (res && res.message) || "没有需要归类的流水";
    },
    task: async (update) => {
      const res = await classifyBills("unmatched");
      if (res && res.processed > 0) update("归类完成，正在刷新列表…");
      return res;
    },
  });
  if (res) load();
}

watch(
  () => store.tab === "bills",
  (active) => {
    if (!active) return;
    /* 问账「存为筛选」交接：读后清空，避免刷新时重复套用 */
    const handoff = billsFilterHandoff.value;
    if (handoff) {
      billsFilterHandoff.value = null;
      Object.assign(filters, {
        start: handoff.start || "",
        end: handoff.end || "",
        tx_type: handoff.tx_type || "",
        categories: handoff.categories || [],
        merchants: handoff.merchants || [],
      });
      page.value = 1;
      advancedOpen.value = true;
    }
    load();
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
        <button v-if="recycleMode" class="btn ghost danger" @click="emptyRecycleNow">清空回收站</button>
      </div>
    </div>

    <!-- 问账带入的多值口径 chip：无常规控件对应，单独展示与清除 -->
    <div v-if="!recycleMode && handoffScopeLabel" class="handoff-bar">
      <span class="handoff-bar__label">问账口径</span>
      <span class="handoff-bar__scope">{{ handoffScopeLabel }}</span>
      <button class="btn mini ghost" @click="clearHandoffScope">清除口径</button>
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
              <th
v-for="c in columns" :key="c.key" :class="{ num: c.num, sortable: !recycleMode }"
                  :title="recycleMode ? null : `按${c.label}排序`" @click="!recycleMode && toggleSort(c.key)">
                {{ c.label }}<span class="sort" :class="{ active: !recycleMode && sortBy === c.key }"><AppIcon :name="sortIcon(c.key)" :size="12" /></span>
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
/* 问账口径 chip：来自「存为筛选」的多值筛选无常规控件，单独一行展示 */
.handoff-bar {
  display: flex;
  align-items: center;
  gap: var(--space-1-5);
  margin: var(--space-1) 0;
  padding: var(--space-1) var(--space-1-5);
  border: 1px dashed var(--color-border);
  border-radius: 8px;
  font-size: 0.82rem;
}
.handoff-bar__label {
  flex-shrink: 0;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-size: 0.74rem;
}
.handoff-bar__scope {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-text-secondary);
}

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
