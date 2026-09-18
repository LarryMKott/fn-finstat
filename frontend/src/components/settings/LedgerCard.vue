<script setup>
/* 账本管理卡片（T-7.1）：账本列表、新建、改名、删除。
 * 账本为全应用共享维度：写操作限应用管理员（403 时后端拒绝，按钮不强隐藏，
 * 由 toast 提示无权限）；删除账本前确认，数据会并入默认账本不会丢失。 */
import { computed, onMounted, ref } from "vue";
import { createLedger, deleteLedger, updateLedger } from "../../api/ledger";
import { confirm } from "../../composables/useConfirm";
import { isBusy, runTask } from "../../composables/useLoading";
import { ledgers, refreshLedgers, store } from "../../store";
import { toast } from "../../toast";
import AppIcon from "../AppIcon.vue";

const newName = ref("");
const editingId = ref(null);
const editName = ref("");

const busy = computed(
  () => isBusy("ledger:add") || isBusy("ledger:rename") || isBusy("ledger:delete"),
);

onMounted(() => {
  if (store.tab === "settings") refreshLedgers();
});

async function add() {
  const name = newName.value.trim();
  if (!name) {
    toast("请输入账本名称", true);
    return;
  }
  await runTask({
    key: "ledger:add",
    title: "新增账本",
    detail: `正在创建「${name}」…`,
    rethrow: false,
    successText: "账本已创建，可在流水与看板页切换查看",
    task: async () => {
      await createLedger(name);
      newName.value = "";
      await refreshLedgers();
    },
  });
}

function startEdit(l) {
  editingId.value = l.id;
  editName.value = l.name;
}

async function saveEdit() {
  const name = editName.value.trim();
  if (!name) {
    toast("账本名称不能为空", true);
    return;
  }
  await runTask({
    key: "ledger:rename",
    title: "重命名账本",
    rethrow: false,
    successText: "账本已更新",
    task: async () => {
      await updateLedger(editingId.value, { name });
      editingId.value = null;
      await refreshLedgers();
    },
  });
}

async function remove(l) {
  await runTask({
    key: "ledger:delete",
    title: "删除账本",
    rethrow: false,
    task: async (update) => {
      const msg = l.is_default
        ? "默认账本不可删除"
        : `确定删除账本「${l.name}」吗？其下 ${l.bill_count} 条流水与预算、资产快照将并入默认账本，不会丢失。`;
      if (l.is_default) {
        toast(msg, true);
        return;
      }
      const okToDelete = await confirm({
        title: "删除账本",
        message: msg,
        danger: true,
        confirmText: "删除",
      });
      if (!okToDelete) return;
      update(`正在删除「${l.name}」…`);
      await deleteLedger(l.id);
      await refreshLedgers();
    },
  });
}
</script>

<template>
  <div class="chart-box">
    <div class="section-head">
      <h3><AppIcon name="book" :size="16" /> 账本管理</h3>
      <span class="section-head__hint">
        账本为全应用共享维度；不选账本时流水与统计不按账本过滤
      </span>
    </div>
    <div class="add-row">
      <input
        v-model="newName"
        type="text"
        placeholder="输入新账本名称，如：装修账本"
        maxlength="64"
        aria-label="新账本名称"
        @keydown.enter="add"
      />
      <button class="btn primary" :disabled="busy" @click="add">
        <AppIcon name="plus" :size="15" /> 新增
      </button>
    </div>

    <ul class="ledger-list">
      <li v-for="l in ledgers || []" :key="l.id">
        <template v-if="editingId === l.id">
          <input
            v-model="editName"
            class="cat-edit"
            type="text"
            maxlength="64"
            aria-label="账本名称"
            @keydown.enter="saveEdit"
            @keydown.esc="editingId = null"
          />
          <span class="cat-actions">
            <button class="btn mini" :disabled="busy" @click="saveEdit">保存</button>
            <button class="btn mini" @click="editingId = null">取消</button>
          </span>
        </template>
        <template v-else>
          <span class="ledger-name">
            {{ l.name }}
            <span v-if="l.is_default" class="default-badge">默认</span>
          </span>
          <span class="ledger-count">{{ l.bill_count }} 条流水</span>
          <span class="cat-actions">
            <button class="btn mini" :disabled="busy" @click="startEdit(l)">编辑</button>
            <button
              class="btn mini danger"
              :disabled="busy || l.is_default"
              :title="l.is_default ? '默认账本不可删除' : ''"
              @click="remove(l)"
            >
              删除
            </button>
          </span>
        </template>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.ledger-list {
  box-shadow: none;
  border: none;
  padding: 0;
}
.ledger-name {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ledger-count {
  color: var(--color-text-3, #999);
  font-size: 12px;
  margin-left: auto;
}
.default-badge {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 12px;
  background: var(--color-primary-soft, #e6f4ea);
  color: var(--color-primary, #1a7f37);
}
</style>
