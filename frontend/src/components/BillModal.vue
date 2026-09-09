<script setup>
import { reactive, watch } from "vue";
import { api } from "../api";
import { emptyForm, normalizeTxTime, nowLocalMinute } from "../format";
import { categories, refreshCategories } from "../store";
import { toast } from "../toast";

const props = defineProps({
  show: Boolean,
  /* null 表示新增，否则为待编辑的账单记录 */
  bill: { type: Object, default: null },
});
const emit = defineEmits(["close", "saved"]);

const form = reactive(emptyForm());

watch(
  () => props.show,
  async (show) => {
    if (!show) return;
    if (!categories.value.length) {
      try {
        await refreshCategories();
      } catch (e) {
        /* 忽略，下拉留空 */
      }
    }
    const b = props.bill;
    Object.assign(
      form,
      emptyForm(),
      b
        ? {
            /* 库内为 "YYYY-MM-DD HH:MM:SS"，datetime-local 需要 "T" 分隔的分钟精度 */
            tx_time: (b.tx_time || "").replace(" ", "T").slice(0, 16),
            account: b.account,
            tx_type: b.tx_type,
            merchant: b.merchant,
            amount: b.amount,
            category: categories.value.some((c) => c.name === b.category) ? b.category : "其他",
            tx_id: b.tx_id || "",
            remark: b.remark,
          }
        : { tx_time: nowLocalMinute() },
    );
  },
  { immediate: true },
);

async function submit() {
  const amount = Number(form.amount);
  if (!isFinite(amount) || amount <= 0) {
    toast("请输入有效金额", true);
    return;
  }
  const payload = {
    tx_time: normalizeTxTime(form.tx_time),
    account: form.account,
    tx_type: form.tx_type,
    merchant: form.merchant.trim(),
    amount,
    category: form.category,
    tx_id: form.tx_id.trim(),
    remark: form.remark.trim(),
  };
  try {
    if (props.bill) {
      await api("/api/bill/" + props.bill.id, { method: "PUT", body: JSON.stringify(payload) });
      toast("已更新");
    } else {
      await api("/api/bill", { method: "POST", body: JSON.stringify(payload) });
      toast("已新增");
    }
    emit("close");
    emit("saved");
  } catch (err) {
    toast(err.message, true);
  }
}
</script>

<template>
  <div class="modal-mask" :class="{ show }" @click.self="emit('close')">
    <div class="modal">
      <h3>{{ bill ? "编辑流水" : "新增流水" }}</h3>
      <form @submit.prevent="submit">
        <label>交易时间
          <input v-model="form.tx_time" type="datetime-local" required />
        </label>
        <label>账户
          <select v-model="form.account">
            <option value="wechat">微信</option>
            <option value="alipay">支付宝</option>
          </select>
        </label>
        <label>收支类型
          <select v-model="form.tx_type">
            <option value="expense">支出</option>
            <option value="income">收入</option>
            <option value="transfer">转账</option>
          </select>
        </label>
        <label>商户名称
          <input v-model="form.merchant" type="text" maxlength="100" placeholder="如：某某餐厅" />
        </label>
        <label>金额（元）
          <input v-model="form.amount" type="number" step="0.01" min="0.01" required placeholder="0.00" />
        </label>
        <label>分类
          <select v-model="form.category">
            <option v-for="c in categories" :key="c.id" :value="c.name">{{ c.name }}</option>
          </select>
        </label>
        <label>交易单号
          <input v-model="form.tx_id" type="text" maxlength="64" placeholder="可选" />
        </label>
        <label>备注
          <input v-model="form.remark" type="text" maxlength="200" placeholder="可选" />
        </label>
        <div class="modal-actions">
          <button type="button" class="btn" @click="emit('close')">取消</button>
          <button type="submit" class="btn primary">保存</button>
        </div>
      </form>
    </div>
  </div>
</template>
