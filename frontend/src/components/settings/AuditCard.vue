<script setup>
/* 设置-操作审计（T-7.6）：业务写操作留痕查询。
 * 权限与后端一致：管理员可看全部账号（可按操作人过滤），普通账号仅自己的操作。 */
import { computed, reactive, ref, watch } from "vue";
import { auditLogs } from "../../api/audit";
import { store } from "../../store";
import { isBusy, runTask } from "../../composables/useLoading";

const LIMIT = 200;
const state = reactive({ total: 0, items: [] });
const loaded = ref(false);
const filterAction = ref("");
const filterUser = ref("");
const loading = computed(() => isBusy("audit:load"));

/* 动作 → 中文分组标签（前缀即业务域） */
const ACTION_LABELS = {
  bill: "流水",
  category: "分类",
  ledger: "账本",
  budget: "预算",
  asset: "资产快照",
  family: "家庭",
  reimb: "报销单",
  loan: "借贷",
  rules: "学习规则",
  backup: "备份恢复",
  db: "数据库",
};
const actionLabel = (action) => {
  const domain = action.split(".")[0];
  return (ACTION_LABELS[domain] || domain) + " " + action.split(".").slice(1).join(".");
};

const fmtTime = (epoch) => {
  const d = new Date(epoch * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

async function load() {
  await runTask({
    key: "audit:load",
    title: "加载操作审计",
    mode: "latest",
    rethrow: false,
    task: async () => {
      const params = { limit: LIMIT };
      if (filterAction.value) params.action = filterAction.value.trim();
      if (filterUser.value.trim()) params.user_id = filterUser.value.trim();
      const data = await auditLogs(params);
      state.total = data.total;
      state.items = data.items;
      loaded.value = true;
    },
  });
}

watch(
  () => store.tab === "settings",
  (active) => {
    if (active && !loaded.value) load();
  },
  { immediate: true },
);
</script>

<template>
  <div class="settings-box">
    <div class="section-head">
      <h3>操作审计</h3>
      <span class="section-head__hint">
        业务写操作留痕（谁在什么时候动了什么）；保留 {{ 90 }} 天。
        管理员可查全部账号，普通账号仅自己的操作
      </span>
    </div>
    <div class="log-toolbar">
      <input
        v-model="filterAction"
        type="text"
        placeholder="按动作过滤，如 bill.update（可留空）"
        aria-label="动作过滤"
        @keydown.enter="load"
      />
      <input
        v-model="filterUser"
        type="text"
        placeholder="按操作人 id 过滤（管理员）"
        aria-label="操作人过滤"
        @keydown.enter="load"
      />
      <button class="btn" :disabled="loading" @click="load">
        {{ loading ? "加载中…" : "查询" }}
      </button>
      <span v-if="loaded" class="log-meta">共 {{ state.total }} 条，显示最近 {{ state.items.length }} 条</span>
    </div>
    <table v-if="state.items.length" class="audit-table">
      <thead>
        <tr>
          <th>时间</th>
          <th>操作人</th>
          <th>动作</th>
          <th>摘要</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="i in state.items" :key="i.id">
          <td class="audit-time">{{ fmtTime(i.created_at) }}</td>
          <td>{{ i.user_id || "-" }}</td>
          <td>{{ actionLabel(i.action) }}</td>
          <td class="audit-summary">{{ i.summary }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else-if="loaded" class="hint">暂无审计记录</p>
  </div>
</template>

<style scoped>
.audit-table {
  width: 100%;
  font-size: 13px;
  border-collapse: collapse;
}
.audit-table th,
.audit-table td {
  text-align: left;
  padding: 4px 8px;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
  vertical-align: top;
}
.audit-time {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  opacity: 0.8;
}
.audit-summary {
  word-break: break-all;
}
</style>
