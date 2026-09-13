<script setup>
/* 应用内确认弹窗：全局单例（配合 composables/useConfirm.js 使用），复用全局 modal 样式 */
import { nextTick, ref, watch } from "vue";
import { confirmState, confirmAccept, confirmCancel } from "../composables/useConfirm";

/* 键盘可达性（对齐被替换的原生 confirm）：Esc 取消，打开时聚焦取消按钮，关闭后归还焦点 */
const cancelBtn = ref(null);
let lastFocused = null;

watch(
  () => confirmState.visible,
  (visible) => {
    if (visible) {
      lastFocused = document.activeElement;
      nextTick(() => cancelBtn.value?.focus());
    } else if (lastFocused instanceof HTMLElement) {
      lastFocused.focus();
      lastFocused = null;
    }
  }
);
</script>

<template>
  <Teleport to="body">
    <div
      class="modal-mask"
      :class="{ show: confirmState.visible }"
      @click.self="confirmCancel"
    >
      <div
        class="confirm"
        role="alertdialog"
        aria-modal="true"
        :aria-label="confirmState.title"
        @keydown.esc="confirmCancel"
      >
        <h3 class="confirm__title">{{ confirmState.title }}</h3>
        <p class="confirm__message">{{ confirmState.message }}</p>
        <div class="confirm__actions">
          <button ref="cancelBtn" class="btn" @click="confirmCancel">{{ confirmState.cancelText }}</button>
          <button
            class="btn"
            :class="confirmState.danger ? 'danger' : 'primary'"
            @click="confirmAccept"
          >
            {{ confirmState.confirmText }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.confirm {
  width: 360px;
  max-width: 100%;
  padding: var(--space-6);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-xl);
  animation: modal-in var(--dur-slow) var(--ease-out);
}
.confirm__title {
  margin: 0 0 var(--space-2);
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text);
}
.confirm__message {
  margin: 0 0 var(--space-5);
  font-size: 13px;
  line-height: 1.6;
  color: var(--color-text-secondary);
  white-space: pre-line;
}
.confirm__actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
</style>
