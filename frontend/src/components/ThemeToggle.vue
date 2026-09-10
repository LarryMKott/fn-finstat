<script setup>
import { computed } from "vue";
import { cycleTheme, themeMode } from "../theme";

/* 每种模式的图标（Feather 风格 inline SVG）与提示文案，点击循环切换到下一模式 */
const MODE_META = {
  auto: { label: "跟随系统", next: "浅色", icon: "M2 3h20v14H2z M8 21h8 M12 17v4" },
  light: { label: "浅色", next: "深色", icon: "M12 7a5 5 0 1 0 0 10a5 5 0 0 0 0-10z M12 1v2 M12 21v2 M4.22 4.22l1.42 1.42 M18.36 18.36l1.42 1.42 M1 12h2 M21 12h2 M4.22 19.78l1.42-1.42 M18.36 5.64l1.42-1.42" },
  dark: { label: "深色", next: "跟随系统", icon: "M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" },
};

const meta = computed(() => MODE_META[themeMode.value] || MODE_META.auto);
const title = computed(() => `主题：${meta.value.label}，点击切换${meta.value.next}`);
</script>

<template>
  <button class="theme-btn" type="button" :title="title" @click="cycleTheme">
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <path :d="meta.icon" />
    </svg>
  </button>
</template>
