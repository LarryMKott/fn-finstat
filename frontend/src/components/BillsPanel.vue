<script setup>
import { computed, reactive, ref, watch } from "vue";
import { api } from "../api";
import { fmtAccount, fmtMoney, fmtType, tagClass } from "../format";
import { categories, store } from "../store";
import { toast } from "../toast";
import BillModal from "./BillModal.vue";

const PAGE_SIZE = 20;

/* 可排序列（与后端白名单一致；备注列保持第 7 列，移动端样式依赖列序） */
const columns = [
  { key: "tx_time", label: "交易时间" },
  { key: "account", label: "账户" },
  { key: "tx_type", label: "类型" },
  { key: "merchant", label: "商户" },
  { key: "amount", label: "金额", num: true },
  { key: "category", label: "分类" },
  { key: "remark", label: "备注" },
];

const filters = reactive({ start: "", end: "", account: "", tx_type: "", category: "" });
const bills = ref([]);
const total = ref(0);
const page = ref(1);
const sortBy = ref("tx_time");
const sortOrder = ref("desc");
const modal = ref({ show: false, bill: null });

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / PAGE_SIZE)));

async function load() {
  try {
    const params = new URLSearchParams({
      page: page.value,
      page_size: PAGE_SIZE,
      sort_by: sortBy.value,
      order: sortOrder.value,
    });
    for (const [k, v] of Object.entries(filters)) {
      if (v) params.set(k, v);
    }
    const data = await api("/api/bill/list?" + params.toString());
    bills.value = data.items;
    total.value = data.total;
  } catch (err) {
    toast("流水加载失败：" + err.message, true);
  }
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
  Object.assign(filters, { start: "", end: "", account: "", tx_type: "", category: "" });
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
  if (!confirm(`确定删除「${bill.merchant || bill.tx_time}」这条流水吗？`)) return;
  try {
    await api("/api/bill/" + bill.id, { method: "DELETE" });
    toast("已删除");
    /* 当前页只剩这一条时回退一页，避免停留在空页 */
    if (bills.value.length === 1 && page.value > 1) page.value--;
    load();
  } catch (err) {
    toast(err.message, true);
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
    <div class="filter-bar">
      <input v-model="filters.start" type="date" title="起始日期" />
      <span class="sep">至</span>
      <input v-model="filters.end" type="date" title="结束日期" />
      <select v-model="filters.account" title="账户">
        <option value="">全部账户</option>
        <option value="wechat">微信</option>
        <option value="alipay">支付宝</option>
      </select>
      <select v-model="filters.tx_type" title="收支类型">
        <option value="">全部类型</option>
        <option value="expense">支出</option>
        <option value="income">收入</option>
        <option value="transfer">转账</option>
      </select>
      <select v-model="filters.category" title="消费分类">
        <option value="">全部分类</option>
        <option v-for="c in categories" :key="c.id" :value="c.name">{{ c.name }}</option>
      </select>
      <button class="btn primary" @click="search">查询</button>
      <button class="btn" @click="resetFilters">重置</button>
      <button class="btn primary right" @click="openCreate">+ 新增流水</button>
    </div>

    <div class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th v-for="c in columns" :key="c.key" class="sortable" :class="{ num: c.num }"
                :title="`按${c.label}排序`" @click="toggleSort(c.key)">
              {{ c.label }}<span class="sort" :class="{ active: sortBy === c.key }">{{ sortIcon(c.key) }}</span>
            </th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!bills.length">
            <td colspan="8" class="empty">暂无数据，去「账单导入」上传或手动新增</td>
          </tr>
          <tr v-for="b in bills" :key="b.id">
            <td>{{ b.tx_time }}</td>
            <td>{{ fmtAccount(b.account) }}</td>
            <td><span class="tag" :class="tagClass(b.tx_type)">{{ fmtType(b.tx_type) }}</span></td>
            <td>{{ b.merchant || "-" }}</td>
            <td class="num">{{ fmtMoney(b.amount) }}</td>
            <td>{{ b.category }}</td>
            <td :title="b.remark">{{ (b.remark || "").slice(0, 20) || "-" }}</td>
            <td>
              <button class="btn" @click="openEdit(b)">编辑</button>
              <button class="btn danger" @click="removeBill(b)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="pager">
      <button class="btn" :disabled="page <= 1" @click="prevPage">上一页</button>
      <span>第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 条</span>
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
