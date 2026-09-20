<script setup>
/* 报销 / 垫付工作流（T-7.4）：报销单列表 + 状态流转 + 到账登记 + 勾选流水挂单。
 * 与后端同口径：报销支出仍计入统计，本卡片只跟踪回收进度；
 * bills.reimbursed 标记由后端随挂/摘同步，流水页的报销筛选不受影响。 */
import { computed, reactive, ref } from "vue";
import {
  attachBillsToReimb,
  createReimbursement,
  deleteReimbursement,
  detachBillsFromReimb,
  listReimbursements,
  reimbBills,
  updateReimbursement,
} from "../api/reimb";
import { confirm } from "../composables/useConfirm";
import { isBusy, runTask } from "../composables/useLoading";
import { fmtMoney } from "../utils/format";
import { toast } from "../toast";
import AppIcon from "./AppIcon.vue";

const props = defineProps({
  /* 流水页当前勾选的流水 id 数组（用于「加入报销单」） */
  selectedIds: { type: Array, default: () => [] },
});
const emit = defineEmits(["changed"]);

const claims = ref(null); // null = 未加载
const open = reactive({}); // claim_id -> 是否展开流水明细
const details = reactive({}); // claim_id -> 明细数据
const forms = reactive({}); // claim_id -> 状态/到账编辑表单
const newTitle = ref("");

const hasSelection = computed(() => props.selectedIds.length > 0);

const STATUS = [
  { value: "pending", label: "待提交" },
  { value: "submitted", label: "已提交" },
  { value: "partial", label: "部分到账" },
  { value: "settled", label: "已结清" },
];
const statusLabel = (v) => STATUS.find((s) => s.value === v)?.label || v;

function formOf(claim) {
  if (!forms[claim.id]) {
    forms[claim.id] = reactive({
      status: claim.status,
      received_amount: claim.received_amount ?? "",
      received_date: claim.received_date ?? "",
    });
  }
  return forms[claim.id];
}

function needsAmount(status) {
  return status === "partial" || status === "settled";
}

async function load() {
  await runTask({
    key: "reimb:load",
    title: "加载报销单",
    mode: "latest",
    silent: true,
    rethrow: false,
    task: async () => {
      claims.value = await listReimbursements();
    },
  });
}

async function doCreate() {
  const title = newTitle.value.trim();
  if (!title) {
    toast("请输入报销单名称", true);
    return;
  }
  await runTask({
    key: "reimb:create",
    title: "新建报销单",
    rethrow: false,
    successText: "报销单已创建，勾选流水后可加入",
    task: async () => {
      await createReimbursement(title);
      newTitle.value = "";
      await load();
    },
  });
}

async function doSave(claim) {
  const form = formOf(claim);
  const payload = { status: form.status };
  if (needsAmount(form.status)) {
    if (form.received_amount === "" || form.received_amount == null) {
      // Number("") === 0 会把留空静默当成 0 元到账，必须单独拦截
      toast("该状态需要登记到账金额", true);
      return;
    }
    const amount = Number(form.received_amount);
    if (!isFinite(amount) || amount < 0) {
      toast("请输入有效的到账金额", true);
      return;
    }
    payload.received_amount = amount;
    payload.received_date = form.received_date || null;
  }
  await runTask({
    key: `reimb:save:${claim.id}`,
    title: "保存报销单",
    rethrow: false,
    successText: "报销单已更新",
    task: async () => {
      await updateReimbursement(claim.id, payload);
      await load();
    },
  });
}

async function doDelete(claim) {
  const ok = await confirm({
    title: "删除报销单",
    message: `删除「${claim.title}」后，其下 ${claim.bill_count} 条流水将退出报销单（报销标记复位），流水本身不受影响。确定删除吗？`,
    danger: true,
    confirmText: "删除",
  });
  if (!ok) return;
  await runTask({
    key: `reimb:del:${claim.id}`,
    title: "删除报销单",
    rethrow: false,
    successText: "已删除",
    task: async () => {
      await deleteReimbursement(claim.id);
      await load();
      emit("changed");
    },
  });
}

async function doAttach(claim) {
  if (!hasSelection.value) {
    toast("请先在流水列表勾选要加入的支出流水", true);
    return;
  }
  await runTask({
    key: `reimb:attach:${claim.id}`,
    title: "加入报销单",
    rethrow: false,
    successText: `已加入 ${props.selectedIds.length} 条流水`,
    task: async () => {
      await attachBillsToReimb(claim.id, [...props.selectedIds]);
      await load();
      if (open[claim.id]) await toggleBills(claim.id, true);
      emit("changed");
    },
  });
}

async function toggleBills(claimId, force = false) {
  const next = force ? true : !open[claimId];
  open[claimId] = next;
  if (!next) return;
  await runTask({
    key: `reimb:bills:${claimId}`,
    title: "加载报销单明细",
    mode: "latest",
    silent: true,
    rethrow: false,
    task: async () => {
      details[claimId] = await reimbBills(claimId);
    },
  });
}

async function doDetach(claimId, billId) {
  await runTask({
    key: `reimb:detach:${billId}`,
    title: "移出报销单",
    rethrow: false,
    successText: "已移出",
    task: async () => {
      await detachBillsFromReimb(claimId, [billId]);
      await load();
      await toggleBills(claimId, true);
      emit("changed");
    },
  });
}

defineExpose({ load });
load();
</script>

<template>
  <div class="chart-box">
    <div class="section-head">
      <h3>报销单</h3>
      <span class="section-head__hint">
        跟踪垫付出的回收进度；报销支出仍计入统计，不改变看板数字
      </span>
    </div>

    <div class="filter-bar reimb-add">
      <input
        v-model="newTitle"
        type="text"
        placeholder="新报销单名称，如：出差报销-9月"
        aria-label="报销单名称"
        @keydown.enter="doCreate"
      />
      <button class="btn mini primary" :disabled="isBusy('reimb:create')" @click="doCreate">
        <AppIcon name="plus" :size="14" /> 新建
      </button>
    </div>

    <div v-if="!claims" class="empty">加载中…</div>
    <div v-else-if="!claims.length" class="empty">
      还没有报销单。新建一张，再把勾选的支出流水加进来
    </div>

    <div v-for="c in claims" :key="c.id" class="reimb-claim">
      <div class="reimb-head">
        <span class="reimb-title">{{ c.title }}</span>
        <span class="reimb-meta">
          {{ c.bill_count }} 笔 · {{ fmtMoney(c.total_amount) }}
          <template v-if="c.received_amount != null">
            · 已到账 {{ fmtMoney(c.received_amount) }}
          </template>
        </span>
        <span class="reimb-status" :class="`st-${c.status}`">{{ c.status_label || statusLabel(c.status) }}</span>
      </div>

      <div class="reimb-ops">
        <select v-model="formOf(c).status" title="报销状态" aria-label="报销状态">
          <option v-for="s in STATUS" :key="s.value" :value="s.value">{{ s.label }}</option>
        </select>
        <template v-if="needsAmount(formOf(c).status)">
          <input
            v-model="formOf(c).received_amount"
            type="number"
            step="0.01"
            min="0"
            placeholder="到账金额"
            aria-label="到账金额"
          />
          <input
            v-model="formOf(c).received_date"
            type="date"
            placeholder="到账日期"
            aria-label="到账日期"
          />
        </template>
        <button class="btn mini primary" :disabled="isBusy(`reimb:save:${c.id}`)" @click="doSave(c)">
          保存
        </button>
        <button
          class="btn mini"
          :disabled="isBusy(`reimb:attach:${c.id}`)"
          :title="hasSelection ? '把流水页勾选的支出流水加入本报销单' : '先在流水列表勾选流水'"
          @click="doAttach(c)"
        >
          加入勾选 ({{ selectedIds.length }})
        </button>
        <button class="btn mini" @click="toggleBills(c.id)">
          {{ open[c.id] ? "收起流水" : "查看流水" }}
        </button>
        <button class="btn mini danger" @click="doDelete(c)">删除</button>
      </div>

      <table v-if="open[c.id] && details[c.id]" class="reimb-bills">
        <thead>
          <tr>
            <th>时间</th>
            <th>商户</th>
            <th>分类</th>
            <th class="num">金额</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in details[c.id].rows" :key="b.id">
            <td>{{ b.tx_time }}</td>
            <td>{{ b.merchant || "-" }}</td>
            <td>{{ b.category }}</td>
            <td class="num">{{ fmtMoney(b.amount) }}</td>
            <td class="num">
              <button class="btn mini danger" @click="doDetach(c.id, b.id)">移出</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.reimb-add input[type="text"] {
  min-width: 14em;
}
.reimb-claim {
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 8px;
  padding: 8px 10px;
  margin-bottom: 8px;
}
.reimb-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.reimb-title {
  font-weight: 600;
}
.reimb-meta {
  font-variant-numeric: tabular-nums;
  opacity: 0.85;
}
.reimb-status {
  margin-left: auto;
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
  background: rgba(0, 0, 0, 0.08);
}
.reimb-status.st-settled {
  background: rgba(46, 139, 87, 0.18);
}
.reimb-status.st-partial {
  background: rgba(232, 131, 58, 0.2);
}
.reimb-ops {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 6px;
}
.reimb-ops input,
.reimb-ops select {
  max-width: 10em;
}
.reimb-bills {
  margin-top: 6px;
  width: 100%;
  font-size: 13px;
}
</style>
