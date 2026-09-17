<script setup>
/* 通知铃铛：顶栏入口 + 未读角标 + 下拉通知面板。
 * 未读数 60s 静默轮询（打开面板时立即刷新）；「全部已读」把本人已读
 * 水位线推到最新，广播类通知的已读状态按账号独立（后端语义见 notify_dao）。 */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { getUnreadCount, listNotifications, markAllRead } from "../api/notify";
import { isBusy, runTask } from "../composables/useLoading";
import { NOTIFY_EVENT_LABELS } from "../utils/constants";
import AppIcon from "./AppIcon.vue";

const POLL_SECONDS = 60;

const open = ref(false);
const unread = ref(0);
const items = ref([]);
const rootEl = ref(null);

const loadingList = () => isBusy("notify:list");
const readingAll = () => isBusy("notify:read-all");
const badgeText = computed(() => (unread.value > 99 ? "99+" : String(unread.value)));

async function refreshBadge() {
  await runTask({
    key: "notify:badge",
    title: "刷新未读数",
    silent: true,
    rethrow: false,
    mode: "latest",
    task: async () => {
      const data = await getUnreadCount();
      unread.value = data.unread;
      return data;
    },
  });
}

async function loadList() {
  await runTask({
    key: "notify:list",
    title: "加载通知",
    detail: "正在读取通知列表…",
    mode: "latest",
    rethrow: false,
    task: async () => {
      try {
        const data = await listNotifications(50);
        items.value = data.items || [];
        unread.value = data.unread;
        return data;
      } catch (err) {
        throw new Error("通知加载失败：" + err.message);
      }
    },
  });
}

async function toggle() {
  open.value = !open.value;
  if (open.value) await loadList();
}

async function readAll() {
  await runTask({
    key: "notify:read-all",
    title: "标记全部已读",
    rethrow: false,
    successText: "已全部标记为已读",
    task: async () => {
      await markAllRead();
      await loadList();
    },
  });
}

const label = (type) => NOTIFY_EVENT_LABELS[type] || "通知";

function fmtTime(epoch) {
  const d = new Date(epoch * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* 点击面板外与 Esc 关闭：全局只挂一次，卸载时清理 */
function onDocClick(e) {
  if (open.value && rootEl.value && !rootEl.value.contains(e.target)) open.value = false;
}
function onKeydown(e) {
  if (e.key === "Escape") open.value = false;
}
function onVisible() {
  if (!document.hidden) refreshBadge();
}

let pollTimer = null;
onMounted(() => {
  refreshBadge();
  pollTimer = setInterval(refreshBadge, POLL_SECONDS * 1000);
  document.addEventListener("click", onDocClick);
  document.addEventListener("keydown", onKeydown);
  document.addEventListener("visibilitychange", onVisible);
});
onBeforeUnmount(() => {
  if (pollTimer) clearInterval(pollTimer);
  document.removeEventListener("click", onDocClick);
  document.removeEventListener("keydown", onKeydown);
  document.removeEventListener("visibilitychange", onVisible);
});
</script>

<template>
  <div ref="rootEl" class="bell-wrap">
    <button
      class="btn ghost icon bell-btn"
      title="通知"
      aria-label="通知"
      @click="toggle"
    >
      <AppIcon name="bell" :size="18" />
      <span v-if="unread > 0" class="bell-badge">{{ badgeText }}</span>
    </button>

    <div v-if="open" class="bell-panel">
      <div class="bell-panel__head">
        <span class="bell-panel__title">通知</span>
        <button
          v-if="unread > 0"
          class="btn mini ghost"
          :disabled="readingAll()"
          @click="readAll"
        >
          {{ readingAll() ? "标记中…" : "全部已读" }}
        </button>
      </div>
      <div class="bell-panel__body">
        <div v-if="loadingList()" class="hint">加载中…</div>
        <div v-else-if="!items.length" class="hint">暂无通知</div>
        <ul v-else class="bell-list">
          <li v-for="n in items" :key="n.id" class="bell-item" :class="{ 'is-unread': !n.read }">
            <span class="bell-item__dot" aria-hidden="true" />
            <div class="bell-item__main">
              <div class="bell-item__top">
                <span class="bell-item__tag">{{ label(n.event_type) }}</span>
                <span class="bell-item__time">{{ fmtTime(n.created_at) }}</span>
              </div>
              <span class="bell-item__title">{{ n.title }}</span>
              <span class="bell-item__content">{{ n.content }}</span>
            </div>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bell-wrap {
  position: relative;
}
.bell-btn {
  position: relative;
}
.bell-badge {
  position: absolute;
  top: -4px;
  right: -4px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: var(--radius-pill);
  background: var(--color-danger);
  color: var(--color-on-primary);
  font-size: 10px;
  line-height: 16px;
  font-weight: 600;
  text-align: center;
  pointer-events: none;
}
.bell-panel {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  width: min(360px, calc(100vw - 32px));
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-dropdown);
  overflow: hidden;
}
.bell-panel__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-divider);
}
.bell-panel__title {
  font-weight: 600;
  font-size: var(--text-sm);
}
.bell-panel__body {
  max-height: 380px;
  overflow-y: auto;
  padding: var(--space-2);
}
.bell-panel__body .hint {
  padding: var(--space-4) var(--space-2);
  text-align: center;
}
.bell-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.bell-item {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-2);
  border-radius: var(--radius-md);
}
.bell-item:hover {
  background: var(--color-surface-hover);
}
.bell-item__dot {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
  background: transparent;
  margin-top: 7px;
  flex-shrink: 0;
}
.bell-item.is-unread .bell-item__dot {
  background: var(--color-primary);
}
.bell-item__main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.bell-item__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}
.bell-item__tag {
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  background: var(--color-surface-sunken);
  border-radius: var(--radius-pill);
  padding: 0 8px;
  line-height: 18px;
}
.bell-item__time {
  font-size: var(--text-xs);
  color: var(--color-text-tertiary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.bell-item__title {
  font-size: var(--text-sm);
  font-weight: 550;
  color: var(--color-text);
}
.bell-item.is-unread .bell-item__title {
  font-weight: 650;
}
.bell-item__content {
  font-size: var(--text-xs);
  color: var(--color-text-secondary);
  word-break: break-all;
}
</style>
