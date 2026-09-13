<script setup>
/* 轻提示：成功与失败用图标+语义色区分，便于在不看文字细节时也能感知结果 */
import { toastState } from "../toast";
import AppIcon from "./AppIcon.vue";
</script>

<template>
  <div
    class="toast"
    :class="{ show: toastState.show, error: toastState.error }"
    role="status"
    aria-live="polite"
  >
    <span v-if="toastState.msg" class="toast__icon">
      <AppIcon :name="toastState.error ? 'trash' : 'restore'" :size="15" />
    </span>
    <span class="toast__msg">{{ toastState.msg }}</span>
  </div>
</template>

<style scoped>
.toast {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.toast__icon {
  display: inline-flex;
  flex-shrink: 0;
  opacity: 0.85;
}
.toast__msg {
  min-width: 0;
}
</style>
