<script setup>
/* 新增 / 编辑流水弹窗。
 * 改造点：字段按「核心 → 补充」分组，必填项在前、低频项在后，
 * 减轻手动记账时的填写负担；收支类型用分段控件替代下拉，一键可切 */
import { computed, reactive, ref, watch } from "vue";
import { createBill, updateBill } from "../api/bill";
import { emptyForm, normalizeTxTime } from "../utils/format";
import { nowLocalMinute } from "../utils/datetime";
import { ACCOUNTS, TX_TYPES } from "../utils/constants";
import { categories, refreshCategories } from "../store";
import { isBusy, runTask } from "../composables/useLoading";
import { toast } from "../toast";

const props = defineProps({
  show: Boolean,
  /* null 表示新增，否则为待编辑的账单记录 */
  bill: { type: Object, default: null },
});
const emit = defineEmits(["close", "saved"]);

const form = reactive(emptyForm());
const categoryDowngraded = ref(false);
const showMore = ref(false);

/* 保存中锁住提交按钮，防止连点产生重复流水 */
const saving = computed(() => isBusy("bill:submit"));

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
    categoryDowngraded.value = false;
    showMore.value = false;
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
            category: (() => {
              /* 分类列表为空（接口失败或未返回）时不做降级判断，原样保留
                 原分类交给后端校验——误降级会把用户的分类静默改成「其他」 */
              if (!categories.value.length) return b.category || "其他";
              const exists = categories.value.some((c) => c.name === b.category);
              if (!exists && b.category) categoryDowngraded.value = true;
              return exists ? b.category : "其他";
            })(),
            tx_id: b.tx_id || "",
            remark: b.remark,
            tags: b.tags || "",
            reimbursed: !!b.reimbursed,
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
    tags: form.tags.trim(),
    reimbursed: form.reimbursed,
  };
  const res = await runTask({
    key: "bill:submit",
    title: props.bill ? "保存流水" : "新增流水",
    detail: "正在写入账单…",
    rethrow: false,
    successText: props.bill ? "已更新" : "已新增",
    task: () => (props.bill ? updateBill(props.bill.id, payload) : createBill(payload)),
  });
  if (!res) return;
  emit("close");
  emit("saved");
}
</script>

<template>
  <div class="modal-mask" :class="{ show }" @click.self="emit('close')">
    <div class="modal" role="dialog" aria-modal="true" :aria-label="bill ? '编辑流水' : '新增流水'">
      <h3>{{ bill ? "编辑流水" : "新增流水" }}</h3>
      <form @submit.prevent="submit">
        <!-- 收支类型：分段控件，比下拉少一次点击 -->
        <div class="field type-switch">
          <span class="field__label">收支类型</span>
          <div class="segmented" role="radiogroup" aria-label="收支类型">
            <button
              v-for="t in TX_TYPES"
              :key="t.value"
              type="button"
              class="segmented__item"
              :class="{ 'is-active': form.tx_type === t.value }"
              role="radio"
              :aria-checked="form.tx_type === t.value"
              @click="form.tx_type = t.value"
            >
              {{ t.label }}
            </button>
          </div>
        </div>

        <!-- 核心字段 -->
        <div class="form-grid form-grid--modal">
          <label class="field">
            金额（元）
            <input v-model="form.amount" type="number" step="0.01" min="0.01" required placeholder="0.00" />
          </label>
          <label class="field">
            账户
            <select v-model="form.account">
              <option v-for="a in ACCOUNTS" :key="a.value" :value="a.value">{{ a.label }}</option>
            </select>
          </label>
          <label class="field">
            交易时间
            <input v-model="form.tx_time" type="datetime-local" required />
          </label>
          <label class="field">
            分类
            <select v-model="form.category">
              <option v-for="c in categories" :key="c.id" :value="c.name">{{ c.name }}</option>
            </select>
          </label>
          <label class="field field--full">
            商户名称
            <input v-model="form.merchant" type="text" maxlength="100" placeholder="如：某某餐厅" />
          </label>
        </div>
        <p v-if="categoryDowngraded" class="hint">原分类已删除，将保存为「其他」</p>

        <!-- 补充字段：默认折叠，需要时再展开 -->
        <button
          class="more-toggle"
          type="button"
          :aria-expanded="showMore"
          @click="showMore = !showMore"
        >
          {{ showMore ? "收起补充信息" : "补充信息（标签、报销、备注）" }}
          <span class="more-toggle__arrow" :class="{ 'is-open': showMore }">▾</span>
        </button>

        <div v-show="showMore" class="form-grid form-grid--modal more-fields">
          <label class="field">
            交易单号
            <input v-model="form.tx_id" type="text" maxlength="64" placeholder="可选" />
          </label>
          <label class="field">
            标签
            <input v-model="form.tags" type="text" maxlength="255" placeholder="逗号分隔，如：出差,报销" />
          </label>
          <label class="field field--full">
            备注
            <input v-model="form.remark" type="text" maxlength="200" placeholder="可选" />
          </label>
          <label class="switch-row field--full">
            <input v-model="form.reimbursed" type="checkbox" />
            标记为需要报销 / 已报销
            <em>配合流水页的报销筛选可快速找出待报销支出</em>
          </label>
        </div>

        <div class="modal-actions">
          <button type="button" class="btn ghost" @click="emit('close')">取消</button>
          <button type="submit" class="btn primary" :disabled="saving">
            {{ saving ? "保存中…" : "保存" }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>

<style scoped>
.field__label {
  font-size: var(--text-sm);
  color: var(--color-text-secondary);
}

/* 分段控件 */
.segmented {
  display: inline-flex;
  gap: 3px;
  padding: 3px;
  border-radius: var(--radius-md);
  background: var(--color-surface-sunken);
  border: 1px solid var(--color-border);
}
.segmented__item {
  padding: var(--space-1-5) var(--space-4);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  font-weight: 500;
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out);
}
.segmented__item:hover {
  color: var(--color-text);
}
.segmented__item.is-active {
  background: var(--color-surface);
  color: var(--color-primary);
  font-weight: 600;
  box-shadow: var(--shadow-xs);
}

.type-switch {
  margin-bottom: var(--space-4);
}

.form-grid--modal {
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}
.field--full {
  grid-column: 1 / -1;
}

.more-toggle {
  display: flex;
  align-items: center;
  gap: var(--space-1-5);
  width: 100%;
  padding: var(--space-2) 0;
  margin-bottom: var(--space-1);
  border: none;
  border-top: 1px dashed var(--color-divider);
  background: transparent;
  color: var(--color-primary);
  font-size: var(--text-sm);
  text-align: left;
  cursor: pointer;
}
.more-toggle:hover {
  text-decoration: underline;
}
.more-toggle__arrow {
  transition: transform var(--dur-base) var(--ease-out);
}
.more-toggle__arrow.is-open {
  transform: rotate(180deg);
}

.more-fields {
  padding-top: var(--space-3);
}

@media (max-width: 860px) {
  .form-grid--modal {
    grid-template-columns: 1fr;
  }
  .segmented {
    width: 100%;
  }
  .segmented__item {
    flex: 1;
  }
}
</style>
