<script setup>
/* 借贷台账（T-7.5）：借出（应收）/ 借入（应付）的本金与还款跟踪。
 * 与流水不强制关联；「还清即结项」由后端按还款合计自动推导。 */
import { onMounted, reactive, ref } from "vue";
import {
  addLoanPayment,
  createLoan,
  deleteLoan,
  deleteLoanPayment,
  loanLedger,
  loanPayments,
} from "../api/loans";
import { confirm } from "../composables/useConfirm";
import { isBusy, runTask } from "../composables/useLoading";
import { fmtMoney } from "../utils/format";
import { todayStr } from "../utils/datetime";
import { store } from "../store";
import { toast } from "../toast";
import AppIcon from "./AppIcon.vue";

const ledger = ref(null);
const loadFailed = ref(false);
const expanded = reactive({}); // loan_id -> 展开还款明细
const payments = reactive({}); // loan_id -> 明细数据
const addForm = reactive({
  direction: "lend",
  counterparty: "",
  principal: "",
  loan_date: todayStr(),
  due_date: "",
  note: "",
});
const payForms = reactive({}); // loan_id -> { amount, pay_date, note }

const today = todayStr();

async function load() {
  const res = await runTask({
    key: "loans:load",
    title: "加载借贷台账",
    mode: "latest",
    silent: true,
    rethrow: false,
    task: async () => {
      ledger.value = await loanLedger();
      loadFailed.value = false;
      return true;
    },
  });
  // rethrow:false 时失败返回 undefined：置错误态给「重试」入口，
  // 否则「加载中…」会永久悬挂
  if (res === undefined) loadFailed.value = true;
}

async function doCreate() {
  const principal = Number(addForm.principal);
  if (!addForm.counterparty.trim()) {
    toast("请填写对方名称", true);
    return;
  }
  if (!isFinite(principal) || principal <= 0) {
    toast("请输入有效本金", true);
    return;
  }
  await runTask({
    key: "loans:create",
    title: "登记借贷",
    rethrow: false,
    successText: "已登记",
    task: async () => {
      await createLoan({
        direction: addForm.direction,
        counterparty: addForm.counterparty.trim(),
        principal,
        loan_date: addForm.loan_date,
        due_date: addForm.due_date || null,
        note: addForm.note.trim(),
      });
      addForm.counterparty = "";
      addForm.principal = "";
      addForm.note = "";
      await load();
    },
  });
}

async function doDelete(loan) {
  const ok = await confirm({
    title: "删除借贷记录",
    message: `删除「${loan.counterparty} ${fmtMoney(loan.principal)}」及其全部还款记录？流水不受影响。`,
    danger: true,
    confirmText: "删除",
  });
  if (!ok) return;
  await runTask({
    key: `loans:del:${loan.id}`,
    title: "删除借贷",
    rethrow: false,
    successText: "已删除",
    task: async () => {
      await deleteLoan(loan.id);
      await load();
    },
  });
}

function payFormOf(loanId) {
  if (!payForms[loanId]) {
    payForms[loanId] = reactive({ amount: "", pay_date: today, note: "" });
  }
  return payForms[loanId];
}

async function doAddPayment(loan) {
  const form = payFormOf(loan.id);
  const amount = Number(form.amount);
  if (!isFinite(amount) || amount <= 0) {
    toast("请输入有效还款金额", true);
    return;
  }
  await runTask({
    key: `loans:pay:${loan.id}`,
    title: "登记还款",
    rethrow: false,
    successText: "还款已登记",
    task: async () => {
      const progress = await addLoanPayment(loan.id, {
        amount,
        pay_date: form.pay_date,
        note: form.note.trim(),
      });
      form.amount = "";
      form.note = "";
      if (progress.status === "settled") toast("已还清，自动结项");
      await load();
      await togglePayments(loan.id, true);
    },
  });
}

async function doDeletePayment(loanId, paymentId) {
  const ok = await confirm({
    title: "删除还款记录",
    message: "删除后该笔还款不再计入已还合计，已结清的借条会回到进行中状态。确定删除吗？",
    danger: true,
    confirmText: "删除",
  });
  if (!ok) return;
  await runTask({
    key: `loans:paydel:${paymentId}`,
    title: "删除还款记录",
    rethrow: false,
    successText: "已删除",
    task: async () => {
      await deleteLoanPayment(loanId, paymentId);
      await load();
      await togglePayments(loanId, true);
    },
  });
}

async function togglePayments(loanId, force = false) {
  const next = force ? true : !expanded[loanId];
  expanded[loanId] = next;
  if (!next) return;
  await runTask({
    key: `loans:payments:${loanId}`,
    title: "加载还款明细",
    mode: "latest",
    silent: true,
    rethrow: false,
    task: async () => {
      payments[loanId] = await loanPayments(loanId);
    },
  });
}

onMounted(load);
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'loans' }">
    <div class="panel-head">
      <h2>借贷台账</h2>
      <span class="panel-sub">借出 / 借入与还款进度；独立台账，不影响收支统计</span>
    </div>

    <!-- 应收 / 应付汇总 -->
    <div v-if="ledger" class="summary-cards">
      <div class="sum-card">
        <span class="sum-label">应收未收（借出）</span>
        <span class="sum-value out">{{ fmtMoney(ledger.receivable) }}</span>
      </div>
      <div class="sum-card">
        <span class="sum-label">应付未还（借入）</span>
        <span class="sum-value in">{{ fmtMoney(ledger.payable) }}</span>
      </div>
    </div>

    <!-- 登记新借贷 -->
    <div class="chart-box">
      <div class="section-head">
        <h3>登记借贷</h3>
      </div>
      <div class="filter-bar loan-add">
        <select v-model="addForm.direction" title="借贷方向" aria-label="借贷方向">
          <option value="lend">借出（别人欠我）</option>
          <option value="borrow">借入（我欠别人）</option>
        </select>
        <input
          v-model="addForm.counterparty"
          type="text"
          placeholder="对方名称"
          aria-label="对方名称"
        />
        <input
          v-model="addForm.principal"
          type="number"
          step="0.01"
          min="1"
          placeholder="本金"
          aria-label="本金"
        />
        <input v-model="addForm.loan_date" type="date" title="借贷日期" aria-label="借贷日期" />
        <input v-model="addForm.due_date" type="date" title="约定还款日（可选）" aria-label="约定还款日" />
        <input v-model="addForm.note" type="text" placeholder="备注（可选）" aria-label="备注" />
        <button class="btn mini primary" :disabled="isBusy('loans:create')" @click="doCreate">
          <AppIcon name="plus" :size="14" /> 登记
        </button>
      </div>
    </div>

    <!-- 台账列表 -->
    <div class="chart-box">
      <div v-if="loadFailed" class="empty">
        借贷台账加载失败
        <button class="btn mini primary" style="margin-left: 10px" @click="loadFailed = false; load()">
          重试
        </button>
      </div>
      <div v-else-if="!ledger" class="empty">加载中…</div>
      <div v-else-if="!ledger.items.length" class="empty">还没有借贷记录</div>

      <div v-for="l in ledger?.items || []" :key="l.id" class="loan-item">
        <div class="loan-head">
          <span class="loan-direction" :class="l.direction">
            {{ l.direction === "lend" ? "借出" : "借入" }}
          </span>
          <span class="loan-cp">{{ l.counterparty }}</span>
          <span class="loan-amount">{{ fmtMoney(l.principal) }}</span>
          <span class="reimb-status" :class="l.status === 'settled' ? 'st-settled' : 'st-partial'">
            {{ l.status === "settled" ? "已结清" : "进行中" }}
          </span>
        </div>
        <div class="loan-progress">
          <span class="loan-bar">
            <span
              class="loan-fill"
              :style="{ width: Math.min(100, Math.round((l.repaid / (l.principal || 1)) * 100)) + '%' }"
            ></span>
          </span>
          <span class="loan-num">
            已还 {{ fmtMoney(l.repaid) }} / 余 {{ fmtMoney(l.remaining) }}
            <template v-if="l.due_date"> · 还款日 {{ l.due_date }}</template>
          </span>
        </div>
        <div v-if="l.note" class="loan-note">{{ l.note }}</div>

        <div class="loan-ops">
          <button class="btn mini" @click="togglePayments(l.id)">
            {{ expanded[l.id] ? "收起还款" : `还款记录 (${l.payment_count})` }}
          </button>
          <button class="btn mini danger" @click="doDelete(l)">删除</button>
        </div>

        <div v-if="expanded[l.id]" class="loan-payments">
          <div class="filter-bar loan-pay-add">
            <input
              v-model="payFormOf(l.id).amount"
              type="number"
              step="0.01"
              min="0.01"
              placeholder="还款金额"
              aria-label="还款金额"
            />
            <input v-model="payFormOf(l.id).pay_date" type="date" title="还款日期" aria-label="还款日期" />
            <input v-model="payFormOf(l.id).note" type="text" placeholder="备注（可选）" aria-label="还款备注" />
            <button
              class="btn mini primary"
              :disabled="isBusy(`loans:pay:${l.id}`)"
              @click="doAddPayment(l)"
            >
              登记还款
            </button>
          </div>
          <table v-if="payments[l.id]" class="loan-pay-table">
            <thead>
              <tr>
                <th>日期</th>
                <th class="num">金额</th>
                <th>备注</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in payments[l.id].rows" :key="p.id">
                <td>{{ p.pay_date }}</td>
                <td class="num">{{ fmtMoney(p.amount) }}</td>
                <td>{{ p.note || "-" }}</td>
                <td class="num">
                  <button class="btn mini danger" @click="doDeletePayment(l.id, p.id)">删除</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.loan-add input[type="text"],
.loan-add input[type="number"] {
  min-width: 8em;
}
.loan-item {
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 10px;
}
.loan-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.loan-direction {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
  background: rgba(0, 0, 0, 0.08);
}
.loan-direction.lend {
  background: rgba(214, 69, 69, 0.15);
}
.loan-direction.borrow {
  background: rgba(46, 139, 87, 0.15);
}
.loan-cp {
  font-weight: 600;
}
.loan-amount {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.loan-progress {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
}
.loan-bar {
  flex: 1;
  max-width: 320px;
  height: 8px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.08);
  overflow: hidden;
  display: inline-block;
}
.loan-fill {
  display: block;
  height: 100%;
  border-radius: 4px;
  background: var(--accent, #e8833a);
}
.loan-num {
  font-variant-numeric: tabular-nums;
  opacity: 0.85;
}
.loan-note {
  margin-top: 4px;
  font-size: 13px;
  opacity: 0.75;
}
.loan-ops {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}
.loan-payments {
  margin-top: 8px;
}
.loan-pay-add input[type="text"],
.loan-pay-add input[type="number"] {
  min-width: 8em;
}
.loan-pay-table {
  margin-top: 6px;
  width: 100%;
  font-size: 13px;
}
</style>
