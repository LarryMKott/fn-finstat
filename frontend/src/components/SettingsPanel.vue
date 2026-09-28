<script setup>
/* 设置页外壳：分组锚点导航 + 子卡片组合。
 * 设置项增多后由「10 张卡片一屏平铺」改为 4 个业务域分组（通用 / 智能与自动化 /
 * 数据与备份 / 安全与日志），顶部吸顶 chip 导航点击平滑滚动定位，滚动时反向
 * 高亮当前分组（scrollspy）。子卡片不变（components/settings/ 各自管理状态
 * 与加载时机），分组只是导航与视觉分层，不引入新的数据流。 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { store } from "../store";
import DatabaseCard from "./settings/DatabaseCard.vue";
import AICard from "./settings/AICard.vue";
import BackupCard from "./settings/BackupCard.vue";
import LogsCard from "./settings/LogsCard.vue";
import AuditCard from "./settings/AuditCard.vue";
import TokenCard from "./settings/TokenCard.vue";
import AppearanceAboutCard from "./settings/AppearanceAboutCard.vue";
import AutomationCard from "./settings/AutomationCard.vue";
import NotificationCard from "./settings/NotificationCard.vue";
import LedgerCard from "./settings/LedgerCard.vue";
import AppIcon from "./AppIcon.vue";

/* 分组顺序即页面顺序：常用在前（外观/关于 → 智能配置 → 数据生命周期 → 安全运维） */
const GROUPS = [
  { id: "general", label: "通用", icon: "settings", desc: "外观主题与应用信息" },
  { id: "ai", label: "智能与自动化", icon: "sparkles", desc: "AI 接入、定时任务与通知推送" },
  { id: "data", label: "数据与备份", icon: "database", desc: "数据库、账本与备份恢复" },
  { id: "security", label: "安全与日志", icon: "shield", desc: "API 凭证、操作审计与运行日志" },
];

const activeGroup = ref(GROUPS[0].id);
const navEl = ref(null);

function scrollToGroup(id) {
  activeGroup.value = id;
  /* 平滑滚动期间 scrollspy 会途经中间分组，短暂锁住反向高亮，避免 chip 闪烁 */
  spyLockUntil = Date.now() + 700;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document
    .getElementById(`settings-group-${id}`)
    ?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
}

/* scrollspy：取「分组标题已滚过吸顶导航下缘」的最后一个分组为当前项；
 * 页面滚到底时强制命中最后一组（末组较短时顶部可能永远够不到探测线）。
 * 窗口级滚动（.app-main 无滚动容器），rAF 节流即可。 */
let ticking = false;
let spyLockUntil = 0;

function computeActive() {
  if (store.tab !== "settings" || !navEl.value) return;
  const probe = navEl.value.getBoundingClientRect().bottom + 32;
  let current = GROUPS[0].id;
  for (const g of GROUPS) {
    const el = document.getElementById(`settings-group-${g.id}`);
    if (el && el.getBoundingClientRect().top <= probe) current = g.id;
  }
  const doc = document.documentElement;
  const atBottom = window.innerHeight + Math.ceil(window.scrollY) >= doc.scrollHeight - 2;
  if (atBottom) current = GROUPS[GROUPS.length - 1].id;
  activeGroup.value = current;
}

function onScroll() {
  if (ticking) return;
  ticking = true;
  requestAnimationFrame(() => {
    ticking = false;
    if (Date.now() < spyLockUntil) return;
    computeActive();
  });
}

watch(
  () => store.tab,
  (tab) => {
    /* display:none → block 后位置测量才有效 */
    if (tab === "settings") nextTick(computeActive);
  },
);

onMounted(() => {
  window.addEventListener("scroll", onScroll, { passive: true });
  nextTick(computeActive);
});
onBeforeUnmount(() => window.removeEventListener("scroll", onScroll));
</script>

<template>
  <section class="panel" :class="{ active: store.tab === 'settings' }">
    <nav ref="navEl" class="settings-nav" aria-label="设置分组导航">
      <button
        v-for="g in GROUPS"
        :key="g.id"
        type="button"
        class="settings-nav__chip"
        :class="{ 'is-active': activeGroup === g.id }"
        :aria-current="activeGroup === g.id ? 'true' : null"
        @click="scrollToGroup(g.id)"
      >
        <AppIcon :name="g.icon" :size="15" />
        {{ g.label }}
      </button>
    </nav>

    <section
      id="settings-group-general"
      class="settings-group"
      aria-labelledby="settings-group-general-title"
    >
      <div class="settings-group__head">
        <span class="settings-group__icon"><AppIcon name="settings" :size="17" /></span>
        <h2 id="settings-group-general-title">通用</h2>
        <span class="settings-group__desc">外观主题与应用信息</span>
      </div>
      <AppearanceAboutCard />
    </section>

    <section
      id="settings-group-ai"
      class="settings-group"
      aria-labelledby="settings-group-ai-title"
    >
      <div class="settings-group__head">
        <span class="settings-group__icon"><AppIcon name="sparkles" :size="17" /></span>
        <h2 id="settings-group-ai-title">智能与自动化</h2>
        <span class="settings-group__desc">AI 接入、定时任务与通知推送</span>
      </div>
      <AICard />
      <AutomationCard />
      <NotificationCard />
    </section>

    <section
      id="settings-group-data"
      class="settings-group"
      aria-labelledby="settings-group-data-title"
    >
      <div class="settings-group__head">
        <span class="settings-group__icon"><AppIcon name="database" :size="17" /></span>
        <h2 id="settings-group-data-title">数据与备份</h2>
        <span class="settings-group__desc">数据库、账本与备份恢复</span>
      </div>
      <DatabaseCard ref="dbCard" />
      <LedgerCard />
      <BackupCard @restored="dbCard?.loadInfo()" />
    </section>

    <section
      id="settings-group-security"
      class="settings-group"
      aria-labelledby="settings-group-security-title"
    >
      <div class="settings-group__head">
        <span class="settings-group__icon"><AppIcon name="shield" :size="17" /></span>
        <h2 id="settings-group-security-title">安全与日志</h2>
        <span class="settings-group__desc">API 凭证、操作审计与运行日志</span>
      </div>
      <TokenCard />
      <AuditCard />
      <LogsCard />
    </section>
  </section>
</template>

<style scoped>
/* 吸顶分组导航：与顶栏同款毛玻璃，贴在顶栏（60px sticky）之下；
 * z 取 sticky 层级之下，滚动时顶栏的模糊背景压住导航上缘 */
.settings-nav {
  position: sticky;
  top: var(--topbar-height);
  z-index: calc(var(--z-sticky) - 1);
  display: flex;
  gap: var(--space-1-5);
  padding: var(--space-2) 0;
  background: color-mix(in srgb, var(--color-bg) 86%, transparent);
  backdrop-filter: saturate(1.4) blur(12px);
  -webkit-backdrop-filter: saturate(1.4) blur(12px);
}
.settings-nav__chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-pill);
  background: var(--color-surface);
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  font-weight: 500;
  cursor: pointer;
  transition:
    background var(--dur-fast) var(--ease-out),
    color var(--dur-fast) var(--ease-out),
    border-color var(--dur-fast) var(--ease-out);
}
.settings-nav__chip:hover {
  color: var(--color-text);
  border-color: var(--color-border-strong);
}
.settings-nav__chip.is-active {
  background: var(--color-primary-soft);
  border-color: transparent;
  color: var(--color-primary);
  font-weight: 600;
}

/* 分组节：标题行只做轻量分层，卡片保持原 .settings-box 视觉 */
.settings-group {
  scroll-margin-top: calc(var(--topbar-height) + 60px);
}
.settings-group + .settings-group {
  margin-top: var(--space-5);
}
.settings-group__head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  margin: 0 0 var(--space-3);
}
.settings-group__icon {
  align-self: center;
  display: inline-flex;
  color: var(--color-text-tertiary);
}
.settings-group__head h2 {
  font-size: var(--text-lg);
  font-weight: 650;
  letter-spacing: -0.01em;
}
.settings-group__desc {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
}

@media (max-width: 860px) {
  .settings-nav {
    overflow-x: auto;
    scrollbar-width: none;
  }
  .settings-nav::-webkit-scrollbar {
    display: none;
  }
  .settings-nav__chip {
    flex-shrink: 0;
  }
}
</style>
