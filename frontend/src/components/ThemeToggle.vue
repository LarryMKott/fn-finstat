<script setup>
/* 主题切换：auto（跟随系统/飞牛）→ light（日间）→ dark（夜间）循环。
 * 跟随状态直接复用 theme.js：auto 且探测到飞牛时，提示文案会标明来源，
 * 让用户知道当前是「真的跟上了飞牛」而不是恰好配色相同。 */
import { computed } from "vue";
import { cycleTheme, followingFnos, themeMode } from "../theme";
import AppIcon from "./AppIcon.vue";

/* 每种模式的图标与提示文案，点击循环切换到下一模式 */
const MODE_META = {
  auto: { label: "跟随系统", next: "日间" },
  light: { label: "日间", next: "夜间" },
  dark: { label: "夜间", next: "跟随系统" },
};

const props = defineProps({
  /* 侧边栏形态：占满宽度并显示当前模式文案，便于低频配置项被发现 */
  wide: { type: Boolean, default: false },
});

const meta = computed(() => MODE_META[themeMode.value] || MODE_META.auto);

/* auto 模式下区分「跟随飞牛」与「跟随系统偏好」，避免用户误判 */
const label = computed(() =>
  themeMode.value === "auto" && followingFnos.value ? "跟随飞牛" : meta.value.label,
);

const iconName = computed(() => {
  if (themeMode.value === "auto") return followingFnos.value ? "host" : "sparkles";
  return themeMode.value === "dark" ? "moon" : "sun";
});

const title = computed(() => {
  const source = themeMode.value === "auto" && followingFnos.value ? "（已跟随飞牛系统）" : "";
  return `主题：${label.value}${source}，点击切换${meta.value.next}`;
});
</script>

<template>
  <button
    class="theme-btn"
    :class="{ 'theme-btn--wide': props.wide }"
    type="button"
    :title="title"
    :aria-label="title"
    @click="cycleTheme"
  >
    <AppIcon :name="iconName" :size="18" />
    <span v-if="props.wide" class="theme-btn__label">主题 · {{ label }}</span>
  </button>
</template>

<style scoped>
.theme-btn--wide {
  width: 100%;
  height: auto;
  min-height: 38px;
  justify-content: flex-start;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-base);
  color: var(--color-text-secondary);
}
.theme-btn__label {
  white-space: nowrap;
}
</style>
