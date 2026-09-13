<script setup>
/* 行内操作菜单：把「编辑 / 删除」等次要操作收进悬浮菜单。
 * 原设计把两个按钮平铺在每行，9 列表格被撑宽且视觉噪音大；
 * 收纳后表格宽度可控，操作依然一键可达（移动端与桌面共用）。 */
import { onBeforeUnmount, onMounted, ref } from "vue";
import AppIcon from "./AppIcon.vue";

defineProps({
  /* 菜单项：{ key, label, icon, danger } */
  items: { type: Array, required: true },
});
const emit = defineEmits(["select"]);

const open = ref(false);
const root = ref(null);

function toggle() {
  open.value = !open.value;
}

function pick(item) {
  open.value = false;
  emit("select", item.key);
}

/* 点击外部或按 Esc 收起，避免菜单残留遮挡表格 */
function onDocClick(e) {
  if (!open.value) return;
  if (root.value && !root.value.contains(e.target)) open.value = false;
}
function onKeydown(e) {
  if (e.key === "Escape") open.value = false;
}

onMounted(() => {
  document.addEventListener("click", onDocClick);
  document.addEventListener("keydown", onKeydown);
});
onBeforeUnmount(() => {
  document.removeEventListener("click", onDocClick);
  document.removeEventListener("keydown", onKeydown);
});
</script>

<template>
  <div ref="root" class="row-actions" :class="{ open }">
    <button
      class="row-actions__trigger"
      type="button"
      :aria-expanded="open"
      aria-haspopup="menu"
      title="更多操作"
      aria-label="更多操作"
      @click.stop="toggle"
    >
      <AppIcon name="more" :size="17" />
    </button>
    <div v-if="open" class="row-actions__menu" role="menu">
      <button
        v-for="item in items"
        :key="item.key"
        class="row-actions__item"
        :class="{ danger: item.danger }"
        type="button"
        role="menuitem"
        @click.stop="pick(item)"
      >
        <AppIcon v-if="item.icon" :name="item.icon" :size="15" />
        {{ item.label }}
      </button>
    </div>
  </div>
</template>
