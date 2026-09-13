<script setup>
/* 设置-外观与关于：主题跟随状态反馈、模式切换、应用信息（两个轻量静态块合并） */
import { computed, onMounted, ref } from "vue";
import { followingFnos, isDark, setTheme, themeMode } from "../../theme";
import { sdkHosted } from "../../fnos";
import { aboutInfo } from "../../api/settings";
import { runTask } from "../../composables/useLoading";
import AppIcon from "../AppIcon.vue";

/* ---- 外观：主题模式与跟随状态 ---- */
const THEME_OPTIONS = [
  { value: "auto", label: "跟随系统", icon: "sparkles" },
  { value: "light", label: "日间", icon: "sun" },
  { value: "dark", label: "夜间", icon: "moon" },
];

/* 状态文案：明确告诉用户当前是「跟上了飞牛」还是「退回系统偏好」 */
const appearanceLabel = computed(() => {
  const scheme = isDark.value ? "夜间模式" : "日间模式";
  return followingFnos.value ? `飞牛 · ${scheme}` : `${scheme}`;
});

/* 通道说明：官方 SDK 可用时优先说明，让用户知道走的是系统推荐通道 */
const appearanceChannel = computed(() => {
  if (sdkHosted.value) return "已通过飞牛官方 SDK 实时接收主题变化，切换即时生效。";
  if (followingFnos.value) return "已自动跟随飞牛的日间 / 夜间设置，切换后界面配色实时同步。";
  return "";
});

/* ---- 关于（应用信息与作者） ---- */
const about = ref(null);

onMounted(() => {
  /* 关于信息是轻量请求，失败静默即可，但同样走统一通道保证不会残留 loading */
  runTask({
    key: "about:load",
    title: "读取应用信息",
    rethrow: false,
    task: async () => {
      about.value = await aboutInfo();
    },
  });
});
</script>

<template>
  <!-- 外观：主题跟随状态。飞牛宿主主题为自动探测，此处仅做可见性反馈 -->
  <div class="settings-box appearance-box">
    <div class="section-head">
      <h3>外观</h3>
      <span class="theme-status" :class="followingFnos ? 'is-fnos' : 'is-system'">
        <AppIcon :name="isDark ? 'moon' : 'sun'" :size="14" />
        {{ appearanceLabel }}
      </span>
    </div>
    <p class="hint appearance-hint">
      {{
        appearanceChannel
          || (themeMode === "auto"
            ? "未检测到飞牛主题设置，当前跟随浏览器 / 系统的深浅色偏好。"
            : "当前为手动指定主题，不再自动跟随；切回「跟随系统」即可恢复自动同步。")
      }}
    </p>
    <div class="theme-picker" role="group" aria-label="主题模式">
      <button
        v-for="opt in THEME_OPTIONS"
        :key="opt.value"
        type="button"
        class="theme-chip"
        :class="{ 'is-active': themeMode === opt.value }"
        :aria-pressed="themeMode === opt.value"
        @click="setTheme(opt.value)"
      >
        <AppIcon :name="opt.icon" :size="15" />
        {{ opt.label }}
      </button>
    </div>
    <p class="hint">
      图标与界面配色均提供日间 / 夜间两套：日间版在浅色背景下醒目，夜间版在深色背景下柔和护眼。
    </p>
  </div>

  <!-- 关于 -->
  <div class="settings-box about-box">
    <div class="section-head">
      <h3>关于</h3>
    </div>
    <template v-if="about">
      <p class="about-line">
        <b>{{ about.app_name }}</b>
        <span class="about-version">v{{ about.version }}</span>
      </p>
      <p class="about-line muted">{{ about.description }}</p>
      <p class="about-line">
        作者：
        <a :href="about.author_url" target="_blank" rel="noopener">{{ about.author }}</a>
      </p>
      <p class="about-line">
        项目地址：
        <a :href="about.repo_url" target="_blank" rel="noopener">{{ about.repo_url }}</a>
      </p>
      <p class="about-line muted">开源协议：MIT</p>
    </template>
  </div>
</template>

<style scoped>
/* ---- 外观：主题跟随状态 ---- */
.theme-status {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1-5);
  padding: var(--space-1) var(--space-2-5);
  border-radius: var(--radius-pill);
  font-size: var(--text-xs);
  font-weight: 600;
  white-space: nowrap;
}
/* 已跟上飞牛：用主色系（积极状态）；退回系统偏好：中性色（不喧宾夺主） */
.theme-status.is-fnos {
  background: var(--color-primary-soft);
  color: var(--color-primary);
}
.theme-status.is-system {
  background: var(--color-surface-sunken);
  color: var(--color-text-secondary);
}

.appearance-hint {
  margin-top: var(--space-1);
}

.theme-picker {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin: var(--space-3) 0 var(--space-2);
}
.theme-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1-5);
  min-height: 34px;
  padding: 0 var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-pill);
  background: var(--color-surface);
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  cursor: pointer;
  transition:
    background var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out);
}
.theme-chip:hover {
  border-color: var(--color-primary);
  color: var(--color-primary);
}
.theme-chip.is-active {
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-weight: 600;
}
.theme-chip:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
}
</style>
