<script setup>
/* 全局任务进度浮层（配合 composables/useLoading.js 使用）
 *
 * 刻意不做遮罩：加载期间页面仍可滚动与切换，只禁用触发按钮（isBusy），
 * 避免"整页被糊住"的阻塞感，也不会因插入元素引起布局跳动。 */
import { computed } from "vue";
import { loadingState, dismissLoading } from "../composables/useLoading";
import AppIcon from "./AppIcon.vue";

const isRunning = computed(() => loadingState.phase === "running");
const isSuccess = computed(() => loadingState.phase === "success");
const isError = computed(() => loadingState.phase === "error");

/* 确定进度走宽度，不确定进度走左右滑动的循环条 */
const barWidth = computed(() =>
  loadingState.progress === null ? null : `${Math.min(100, Math.max(0, loadingState.progress))}%`
);
const hasProgress = computed(() => loadingState.progress !== null);
</script>

<template>
  <Teleport to="body">
    <div
      class="task-hud"
      :class="{ show: loadingState.visible, 'is-success': isSuccess, 'is-error': isError }"
      role="status"
      aria-live="polite"
    >
      <div class="task-hud__card">
        <span class="task-hud__icon" :class="{ 'is-spin': isRunning }">
          <AppIcon v-if="isSuccess" name="check" :size="16" />
          <AppIcon v-else-if="isError" name="alert" :size="16" />
          <span v-else class="task-hud__spinner" aria-hidden="true"></span>
        </span>

        <div class="task-hud__body">
          <div class="task-hud__title">{{ loadingState.title }}</div>
          <div v-if="loadingState.detail" class="task-hud__detail">{{ loadingState.detail }}</div>
          <div v-if="isRunning" class="task-hud__bar" :class="{ 'is-fixed': hasProgress }">
            <i v-if="hasProgress" :style="{ width: barWidth }"></i>
          </div>
        </div>

        <button
          v-if="isError"
          class="task-hud__close"
          type="button"
          @click="dismissLoading"
        >
          关闭
        </button>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.task-hud {
  /* 浮层本身不吃事件，只有卡片可点，保证不遮挡下方内容 */
  position: fixed;
  right: var(--space-5);
  bottom: var(--space-5);
  z-index: var(--z-hud);
  pointer-events: none;
  /* 隐藏时必须彻底移出命中测试：卡片自身是 pointer-events:auto，
     仅靠 opacity:0 会在右下角留下一块看不见却吃点击的区域（移动端是整条）。
     visibility 延迟到淡出结束再切换，保证过渡动画不被打断。 */
  visibility: hidden;
  opacity: 0;
  transform: translateY(8px);
  transition: opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out),
    visibility 0s linear var(--dur-base);
}
.task-hud.show {
  visibility: visible;
  opacity: 1;
  transform: none;
  transition: opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out), visibility 0s;
}

.task-hud__card {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  width: 300px;
  max-width: calc(100vw - var(--space-8));
  padding: var(--space-3) var(--space-4);
  pointer-events: auto;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
}

.task-hud__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  margin-top: 1px;
  flex-shrink: 0;
  color: var(--color-primary);
}
.task-hud__icon.is-spin {
  color: var(--color-primary);
}
.is-success .task-hud__icon {
  color: var(--color-success);
}
.is-error .task-hud__icon {
  color: var(--color-danger);
}

/* 纯 CSS 环形 spinner：只有 border-color 参与动画，开销极低 */
.task-hud__spinner {
  display: block;
  width: 15px;
  height: 15px;
  border: 2px solid var(--color-border);
  border-top-color: currentColor;
  border-radius: 50%;
  animation: task-hud-spin 720ms linear infinite;
}
@keyframes task-hud-spin {
  to {
    transform: rotate(360deg);
  }
}

.task-hud__body {
  flex: 1 1 auto;
  min-width: 0;
}
.task-hud__title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
  line-height: var(--leading-snug);
}
.task-hud__detail {
  margin-top: 2px;
  font-size: var(--text-xs);
  line-height: 1.5;
  color: var(--color-text-secondary);
  overflow-wrap: anywhere;
}

.task-hud__bar {
  position: relative;
  height: 2px;
  margin-top: var(--space-2);
  border-radius: var(--radius-pill);
  background: var(--color-border);
  overflow: hidden;
}
.task-hud__bar::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: var(--color-primary);
  transform-origin: left center;
  animation: task-hud-indeterminate 1.1s var(--ease-in-out) infinite;
}
/* 有确定进度时关掉循环动画，改由内层条控制宽度 */
.task-hud__bar.is-fixed::after {
  display: none;
}
.task-hud__bar i {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--color-primary);
  transition: width var(--dur-base) var(--ease-out);
}
@keyframes task-hud-indeterminate {
  0% {
    transform: translateX(-100%) scaleX(0.35);
  }
  50% {
    transform: translateX(20%) scaleX(0.55);
  }
  100% {
    transform: translateX(100%) scaleX(0.35);
  }
}

.task-hud__close {
  flex-shrink: 0;
  align-self: center;
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: color var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out);
}
.task-hud__close:hover {
  color: var(--color-danger);
  border-color: var(--color-danger);
}

/* 手机端：避开底部 Tab Bar，改为横向铺满 */
@media (max-width: 860px) {
  .task-hud {
    right: var(--space-4);
    left: var(--space-4);
    bottom: calc(var(--mobile-tabbar-height) + var(--space-3));
  }
  .task-hud__card {
    width: 100%;
    max-width: none;
  }
}
</style>
